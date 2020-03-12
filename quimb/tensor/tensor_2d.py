"""Classes and algorithms related to 2D tensor networks.
"""
import functools
from itertools import product, cycle
from collections import defaultdict

from autoray import do

from ..gen.rand import randn, seed_rand
from ..utils import print_multi_line, check_opt
from .tensor_core import (
    Tensor,
    utup_union,
    tags_to_utup,
    rand_uuid,
    TensorNetwork,
)
from .array_ops import sensibly_scale


class TensorNetwork2D(TensorNetwork):
    r"""Mixin class for tensor networks with a square lattice two-dimensional
    structure, indexed by ``[{row},{column}]`` so that::

                     'COL{j}'
                        v

        i=Lx-1 ●──●──●──●──●──●──   ──●
               |  |  |  |  |  |       |
                     ...
               |  |  |  |  |  | 'I{i},{j}' = 'I3,5' e.g.
        i=3    ●──●──●──●──●──●──
               |  |  |  |  |  |       |
        i=2    ●──●──●──●──●──●──   ──●    <== 'ROW{i}'
               |  |  |  |  |  |  ...  |
        i=1    ●──●──●──●──●──●──   ──●
               |  |  |  |  |  |       |
        i=0    ●──●──●──●──●──●──   ──●

             j=0, 1, 2, 3, 4, 5    j=Ly-1

    This implies the following conventions:

        * the 'up' bond is coordinates ``(i, j), (i + 1, j)``
        * the 'down' bond is coordinates ``(i, j), (i - 1, j)``
        * the 'right' bond is coordinates ``(i, j), (i, j + 1)``
        * the 'left' bond is coordinates ``(i, j), (i, j - 1)``

    """

    _EXTRA_PROPS = (
        '_site_tag_id',
        '_row_tag_id',
        '_col_tag_id',
        '_Lx',
        '_Ly',
    )

    @property
    def Lx(self):
        """The number of rows.
        """
        return self._Lx

    @property
    def Ly(self):
        """The number of columns.
        """
        return self._Ly

    @property
    def site_tag_id(self):
        """The string specifier for tagging each site of this 2D TN.
        """
        return self._site_tag_id

    def site_tag(self, i, j):
        """The name of the tag specifiying the tensor at site ``(i, j)``.
        """
        if not isinstance(i, str):
            i = i % self.Lx
        if not isinstance(j, str):
            j = j % self.Ly
        return self.site_tag_id.format(i, j)

    @property
    def row_tag_id(self):
        """The string specifier for tagging each row of this 2D TN.
        """
        return self._row_tag_id

    def row_tag(self, i):
        if not isinstance(i, str):
            i = i % self.Lx
        return self.row_tag_id.format(i)

    @property
    def row_tags(self):
        """A tuple of all of the ``Lx`` different row tags.
        """
        return tuple(map(self.row_tag, range(self.Lx)))

    @property
    def col_tag_id(self):
        """The string specifier for tagging each column of this 2D TN.
        """
        return self._col_tag_id

    def col_tag(self, j):
        if not isinstance(j, str):
            j = j % self.Ly
        return self.col_tag_id.format(j)

    @property
    def col_tags(self):
        """A tuple of all of the ``Ly`` different column tags.
        """
        return tuple(map(self.col_tag, range(self.Ly)))

    @property
    def site_tags(self):
        """All of the ``Lx * Ly`` site tags.
        """
        return tuple(self.site_tag(i, j)
                     for i in range(self.Lx) for j in range(self.Ly))

    def maybe_convert_coo(self, x):
        """Check if ``x`` is a tuple of two ints and convert to the
        corresponding site tag if so.
        """
        if not isinstance(x, str):
            try:
                i, j = map(int, x)
                return self.site_tag(i, j)
            except (ValueError, TypeError):
                pass
        return x

    def _get_tids_from_tags(self, tags, which='all'):
        """This is the function that lets coordinates such as ``(i, j)`` be
        used for many 'tag' based functions.
        """
        tags = self.maybe_convert_coo(tags)
        return super()._get_tids_from_tags(tags, which=which)

    def gen_site_coos(self):
        """Generate coordinates for all the sites in this 2D TN.
        """
        for i in range(self.Lx):
            for j in range(self.Ly):
                yield (i, j)

    def gen_bond_coos(self):
        """Generate pairs of coordinates for all the bonds in this 2D TN.
        """
        return gen_2d_bond_pairs(self.Lx, self.Ly)

    def canonize_row(self, i, sweep, yrange=None, **canonize_opts):
        r"""Canonize all or part of a row.

        If ``sweep == 'right'`` then::

             |  |  |  |  |  |  |         |  |  |  |  |  |  |
            ─●──●──●──●──●──●──●─       ─●──●──●──●──●──●──●─
             |  |  |  |  |  |  |         |  |  |  |  |  |  |
            ─●──●──●──●──●──●──●─  ==>  ─●──>──>──>──>──o──●─ row=i
             |  |  |  |  |  |  |         |  |  |  |  |  |  |
            ─●──●──●──●──●──●──●─       ─●──●──●──●──●──●──●─
             |  |  |  |  |  |  |         |  |  |  |  |  |  |
                .           .               .           .
                jstart      jstop           jstart      jstop

        If ``sweep == 'left'`` then::

             |  |  |  |  |  |  |         |  |  |  |  |  |  |
            ─●──●──●──●──●──●──●─       ─●──●──●──●──●──●──●─
             |  |  |  |  |  |  |         |  |  |  |  |  |  |
            ─●──●──●──●──●──●──●─  ==>  ─●──o──<──<──<──<──●─ row=i
             |  |  |  |  |  |  |         |  |  |  |  |  |  |
            ─●──●──●──●──●──●──●─       ─●──●──●──●──●──●──●─
             |  |  |  |  |  |  |         |  |  |  |  |  |  |
                .           .               .           .
                jstop       jstart          jstop       jstart

        Does not yield an orthogonal form in the same way as in 1D.

        Parameters
        ----------
        i : int
            Which row to canonize.
        sweep : {'right', 'left'}
            Which direction to sweep in.
        jstart : int or None
            Starting column, defaults to whole row.
        jstop : int or None
            Stopping column, defaults to whole row.
        canonize_opts
            Supplied to ``canonize_between``.
        """
        check_opt('sweep', sweep, ('right', 'left'))

        if yrange is None:
            yrange = (0, self.Ly - 1)

        if sweep == 'right':
            for j in range(min(yrange), max(yrange), +1):
                self.canonize_between((i, j), (i, j + 1), **canonize_opts)

        else:
            for j in range(max(yrange), min(yrange), -1):
                self.canonize_between((i, j), (i, j - 1), **canonize_opts)

    def canonize_column(self, j, sweep, xrange=None, **canonize_opts):
        r"""Canonize all or part of a column.

        If ``sweep='up'`` then::

             |  |  |         |  |  |
            ─●──●──●─       ─●──●──●─
             |  |  |         |  |  |
            ─●──●──●─       ─●──o──●─ istop
             |  |  |   ==>   |  |  |
            ─●──●──●─       ─●──^──●─
             |  |  |         |  |  |
            ─●──●──●─       ─●──^──●─ istart
             |  |  |         |  |  |
            ─●──●──●─       ─●──●──●─
             |  |  |         |  |  |
                .               .
                j               j

        If ``sweep='down'`` then::

             |  |  |         |  |  |
            ─●──●──●─       ─●──●──●─
             |  |  |         |  |  |
            ─●──●──●─       ─●──v──●─ istart
             |  |  |   ==>   |  |  |
            ─●──●──●─       ─●──v──●─
             |  |  |         |  |  |
            ─●──●──●─       ─●──o──●─ istop
             |  |  |         |  |  |
            ─●──●──●─       ─●──●──●─
             |  |  |         |  |  |
                .               .
                j               j

        Does not yield an orthogonal form in the same way as in 1D.

        Parameters
        ----------
        j : int
            Which column to canonize.
        sweep : {'up', 'down'}
            Which direction to sweep in.
        xrange : None or (int, int), optional
            The range of columns to canonize.
        canonize_opts
            Supplied to ``canonize_between``.
        """
        check_opt('sweep', sweep, ('up', 'down'))

        if xrange is None:
            xrange = (0, self.Lx - 1)

        if sweep == 'up':
            for i in range(min(xrange), max(xrange), +1):
                self.canonize_between((i, j), (i + 1, j), **canonize_opts)
        else:
            for i in range(max(xrange), min(xrange), -1):
                self.canonize_between((i, j), (i - 1, j), **canonize_opts)

    def canonize_row_around(self, i, around=(0, 1)):
        # sweep to the right
        self.canonize_row(i, 'right', yrange=(0, min(around)))
        # sweep to the left
        self.canonize_row(i, 'left', yrange=(max(around), self.Ly - 1))

    def compress_row(self, i, sweep, yrange=None, **compress_opts):
        r"""Compress all or part of a row.

        If ``sweep == 'right'`` then::

             |  |  |  |  |  |  |         |  |  |  |  |  |  |
            ━●━━●━━●━━●━━●━━●━━●━       ━●━━●━━●━━●━━●━━●━━●━
             |  |  |  |  |  |  |         |  |  |  |  |  |  |
            ━●━━●━━●━━●━━●━━●━━●━  ━━>  ━●━━>──>──>──>──o━━●━ row=i
             |  |  |  |  |  |  |         |  |  |  |  |  |  |
            ━●━━●━━●━━●━━●━━●━━●━       ━●━━●━━●━━●━━●━━●━━●━
             |  |  |  |  |  |  |         |  |  |  |  |  |  |
                .           .               .           .
                jstart      jstop           jstart      jstop

        If ``sweep == 'left'`` then::

             |  |  |  |  |  |  |         |  |  |  |  |  |  |
            ━●━━●━━●━━●━━●━━●━━●━       ━●━━●━━●━━●━━●━━●━━●━
             |  |  |  |  |  |  |         |  |  |  |  |  |  |
            ━●━━●━━●━━●━━●━━●━━●━  ━━>  ━●━━o──<──<──<──<━━●━ row=i
             |  |  |  |  |  |  |         |  |  |  |  |  |  |
            ━●━━●━━●━━●━━●━━●━━●━       ━●━━●━━●━━●━━●━━●━━●━
             |  |  |  |  |  |  |         |  |  |  |  |  |  |
                .           .               .           .
                jstop       jstart          jstop       jstart

        Does not yield an orthogonal form in the same way as in 1D.

        Parameters
        ----------
        i : int
            Which row to compress.
        sweep : {'right', 'left'}
            Which direction to sweep in.
        jstart : int or None
            Starting column, defaults to whole row.
        jstop : int or None
            Stopping column, defaults to whole row.
        canonize_opts
            Supplied to ``compress_between``.
        """
        check_opt('sweep', sweep, ('right', 'left'))
        compress_opts.setdefault('absorb', 'right')

        if yrange is None:
            yrange = (0, self.Ly - 1)

        if sweep == 'right':
            for j in range(min(yrange), max(yrange), +1):
                self.compress_between((i, j), (i, j + 1), **compress_opts)
        else:
            for j in range(max(yrange), min(yrange), -1):
                self.compress_between((i, j), (i, j - 1), **compress_opts)

    def compress_column(self, j, sweep, xrange=None, **compress_opts):
        r"""Compress all or part of a column.

        If ``sweep='up'`` then::

             ┃  ┃  ┃         ┃  ┃  ┃
            ─●──●──●─       ─●──●──●─
             ┃  ┃  ┃         ┃  ┃  ┃
            ─●──●──●─       ─●──o──●─  .
             ┃  ┃  ┃   ==>   ┃  |  ┃   .
            ─●──●──●─       ─●──^──●─  . xrange
             ┃  ┃  ┃         ┃  |  ┃   .
            ─●──●──●─       ─●──^──●─  .
             ┃  ┃  ┃         ┃  ┃  ┃
            ─●──●──●─       ─●──●──●─
             ┃  ┃  ┃         ┃  ┃  ┃
                .               .
                j               j

        If ``sweep='down'`` then::

             ┃  ┃  ┃         ┃  ┃  ┃
            ─●──●──●─       ─●──●──●─
             ┃  ┃  ┃         ┃  ┃  ┃
            ─●──●──●─       ─●──v──●─ .
             ┃  ┃  ┃   ==>   ┃  |  ┃  .
            ─●──●──●─       ─●──v──●─ . xrange
             ┃  ┃  ┃         ┃  |  ┃  .
            ─●──●──●─       ─●──o──●─ .
             ┃  ┃  ┃         ┃  ┃  ┃
            ─●──●──●─       ─●──●──●─
             ┃  ┃  ┃         ┃  ┃  ┃
                .               .
                j               j

        Does not yield an orthogonal form in the same way as in 1D.

        Parameters
        ----------
        j : int
            Which column to compress.
        sweep : {'up', 'down'}
            Which direction to sweep in.
        xrange : None or (int, int), optional
            The range of rows to compress.
        canonize_opts
            Supplied to ``compress_between``.
        """
        check_opt('sweep', sweep, ('up', 'down'))

        if xrange is None:
            xrange = (0, self.Lx - 1)

        if sweep == 'up':
            compress_opts.setdefault('absorb', 'right')
            for i in range(min(xrange), max(xrange), +1):
                self.compress_between((i, j), (i + 1, j), **compress_opts)
        else:
            compress_opts.setdefault('absorb', 'left')
            for i in range(max(xrange), min(xrange), -1):
                self.compress_between((i - 1, j), (i, j), **compress_opts)

    def __getitem__(self, key):
        """Key based tensor selection, checking for integer based shortcut.
        """
        return super().__getitem__(self.maybe_convert_coo(key))

    def show(self):
        """Print a unicode schematic of this PEPS and its bond dimensions.
        """
        show_2d(self)

    def _contract_boundary_from_bottom_single(
        self,
        xrange,
        yrange,
        canonize=True,
        compress_sweep='left',
        layer_tag=None,
        **compress_opts
    ):
        canonize_sweep = {
            'left': 'right',
            'right': 'left',
        }[compress_sweep]

        for i in range(min(xrange), max(xrange)):
            #
            #     │  │  │  │  │
            #     ●──●──●──●──●       │  │  │  │  │
            #     │  │  │  │  │  -->  ●══●══●══●══●
            #     ●──●──●──●──●
            #
            for j in range(min(yrange), max(yrange) + 1):
                tag1, tag2 = self.site_tag(i, j), self.site_tag(i + 1, j)

                if layer_tag is None:
                    # contract any tensors with coordinates (i + 1, j), (i, j)
                    self.contract_((tag1, tag2), which='any')
                else:
                    # contract a specific pair (i.e. only one 'inner' layer)
                    self.contract_between(tag1, (tag2, layer_tag))

            if canonize:
                #
                #     │  │  │  │  │
                #     ●══●══<══<══<
                #
                self.canonize_row(i, sweep=canonize_sweep, yrange=yrange)

            #
            #     │  │  │  │  │  -->  │  │  │  │  │  -->  │  │  │  │  │
            #     >──●══●══●══●  -->  >──>──●══●══●  -->  >──>──>──●══●
            #     .  .           -->     .  .        -->        .  .
            #
            self.compress_row(i, sweep=compress_sweep,
                              yrange=yrange, **compress_opts)

    def _contract_boundary_from_bottom_multi(
        self,
        xrange,
        yrange,
        layer_tags,
        canonize=True,
        compress_sweep='left',
        **compress_opts
    ):
        for i in range(min(xrange), max(xrange)):
            # make sure the exterior sites are a single tensor
            #
            #    │ ││ ││ ││ ││ │       │ ││ ││ ││ ││ │   (for two layer tags)
            #    ●─○●─○●─○●─○●─○       ●─○●─○●─○●─○●─○
            #    │ ││ ││ ││ ││ │  ==>   ╲│ ╲│ ╲│ ╲│ ╲│
            #    ●─○●─○●─○●─○●─○         ●══●══●══●══●
            #
            for j in range(min(yrange), max(yrange) + 1):
                self ^= (i, j)

            for tag in layer_tags:
                # contract interior sites from layer ``tag``
                #
                #    │ ││ ││ ││ ││ │  (first contraction if there are two tags)
                #    │ ○──○──○──○──○
                #    │╱ │╱ │╱ │╱ │╱
                #    ●══<══<══<══<
                #
                self._contract_boundary_from_bottom_single(
                    xrange=(i, i + 1), yrange=yrange, canonize=canonize,
                    compress_sweep=compress_sweep, layer_tag=tag,
                    **compress_opts)

                # so we can still uniqely identify 'inner' tensors, drop inner
                #     site tag merged into outer tensor for all but last tensor
                for j in range(min(yrange), max(yrange) + 1):
                    inner_tag = self.site_tag(i + 1, j)
                    if len(self.tag_map[inner_tag]) > 1:
                        self[i, j].drop_tags(inner_tag)

    def contract_boundary_from_bottom(
        self,
        xrange,
        yrange=None,
        canonize=True,
        compress_sweep='left',
        layer_tags=None,
        inplace=False,
        **compress_opts
    ):
        """Contract a 2D tensor network inwards from the bottom, canonizing and
        compressing (left to right) along the way.

        Parameters
        ----------
        xrange : (int, int)
            The range of rows to compress (inclusive).
        yrange : (int, int) or None, optional
            The range of columns to compress (inclusive), sweeping along with
            canonization and compression. Defaults to all columns.
        canonize : bool, optional
            Whether to sweep one way with canonization before compressing.
        compress_sweep : {'left', 'right'}, optional
            Which way to perform the compression sweep, which has an effect on
            which tensors end up being canonized.
        layer_tags : None or sequence[str], optional
            If ``None``, all tensors at each coordinate pair
            ``[(i, j), (i + 1, j)]`` will be first contracted. If specified,
            then the outer tensor at ``(i, j)`` will be contracted with the
            tensor specified by ``[(i + 1, j), layer_tag]``, for each
            ``layer_tag`` in ``layer_tags``.
        inplace : bool, optional
            Whether to perform the contraction inplace or not.
        compress_opts
            Supplied to
            :meth:`~quimb.tensor.tensor_2d.TensorNetwork2D.compress_row`.

        See Also
        --------
        contract_boundary_from_top, contract_boundary_from_left,
        contract_boundary_from_right
        """
        tn = self if inplace else self.copy()

        if yrange is None:
            yrange = (0, self.Ly - 1)

        if layer_tags is None:
            tn._contract_boundary_from_bottom_single(
                xrange, yrange, canonize=canonize,
                compress_sweep=compress_sweep, **compress_opts)
        else:
            tn._contract_boundary_from_bottom_multi(
                xrange, yrange, layer_tags, canonize=canonize,
                compress_sweep=compress_sweep, **compress_opts)

        return tn

    contract_boundary_from_bottom_ = functools.partialmethod(
        contract_boundary_from_bottom, inplace=True)

    def _contract_boundary_from_top_single(
        self,
        xrange,
        yrange,
        canonize=True,
        compress_sweep='right',
        layer_tag=None,
        **compress_opts
    ):
        canonize_sweep = {
            'left': 'right',
            'right': 'left',
        }[compress_sweep]

        for i in range(max(xrange), min(xrange), -1):
            #
            #     ●──●──●──●──●
            #     |  |  |  |  |  -->  ●══●══●══●══●
            #     ●──●──●──●──●       |  |  |  |  |
            #     |  |  |  |  |
            #
            for j in range(min(yrange), max(yrange) + 1):
                tag1, tag2 = self.site_tag(i, j), self.site_tag(i - 1, j)
                if layer_tag is None:
                    # contract any tensors with coordinates (i - 1, j), (i, j)
                    self.contract_((tag1, tag2), which='any')
                else:
                    # contract a specific pair
                    self.contract_between(tag1, (tag2, layer_tag))
            if canonize:
                #
                #     ●══●══<══<══<
                #     |  |  |  |  |
                #
                self.canonize_row(i, sweep=canonize_sweep, yrange=yrange)
            #
            #     >──●══●══●══●  -->  >──>──●══●══●  -->  >──>──>──●══●
            #     |  |  |  |  |  -->  |  |  |  |  |  -->  |  |  |  |  |
            #     .  .           -->     .  .        -->        .  .
            #
            self.compress_row(i, sweep=compress_sweep,
                              yrange=yrange, **compress_opts)

    def _contract_boundary_from_top_multi(
        self,
        xrange,
        yrange,
        layer_tags,
        canonize=True,
        compress_sweep='left',
        **compress_opts
    ):
        for i in range(max(xrange), min(xrange), -1):
            # make sure the exterior sites are a single tensor
            #
            #    ●─○●─○●─○●─○●─○         ●══●══●══●══●
            #    │ ││ ││ ││ ││ │  ==>   ╱│ ╱│ ╱│ ╱│ ╱│
            #    ●─○●─○●─○●─○●─○       ●─○●─○●─○●─○●─○
            #    │ ││ ││ ││ ││ │       │ ││ ││ ││ ││ │   (for two layer tags)
            #
            for j in range(min(yrange), max(yrange) + 1):
                self ^= (i, j)

            for tag in layer_tags:
                # contract interior sites from layer ``tag``
                #
                #    ●══<══<══<══<
                #    │╲ │╲ │╲ │╲ │╲
                #    │ ○──○──○──○──○
                #    │ ││ ││ ││ ││ │  (first contraction if there are two tags)
                #
                self._contract_boundary_from_top_single(
                    xrange=(i, i - 1), yrange=yrange, canonize=canonize,
                    compress_sweep=compress_sweep, layer_tag=tag,
                    **compress_opts)

                # so we can still uniqely identify 'inner' tensors, drop inner
                #     site tag merged into outer tensor for all but last tensor
                for j in range(min(yrange), max(yrange) + 1):
                    inner_tag = self.site_tag(i - 1, j)
                    if len(self.tag_map[inner_tag]) > 1:
                        self[i, j].drop_tags(inner_tag)

    def contract_boundary_from_top(
        self,
        xrange,
        yrange=None,
        canonize=True,
        compress_sweep='right',
        layer_tags=None,
        inplace=False,
        **compress_opts
    ):
        """Contract a 2D tensor network inwards from the top, canonizing and
        compressing (left to right) along the way.

        Parameters
        ----------
        xrange : (int, int)
            The range of rows to compress (inclusive).
        yrange : (int, int) or None, optional
            The range of columns to compress (inclusive), sweeping along with
            canonization and compression. Defaults to all columns.
        canonize : bool, optional
            Whether to sweep one way with canonization before compressing.
        compress_sweep : {'right', 'left'}, optional
            Which way to perform the compression sweep, which has an effect on
            which tensors end up being canonized.
        layer_tags : None or str, optional
            If ``None``, all tensors at each coordinate pair
            ``[(i, j), (i - 1, j)]`` will be first contracted. If specified,
            then the outer tensor at ``(i, j)`` will be contracted with the
            tensor specified by ``[(i - 1, j), layer_tag]``, for each
            ``layer_tag`` in ``layer_tags``.
        inplace : bool, optional
            Whether to perform the contraction inplace or not.
        compress_opts
            Supplied to
            :meth:`~quimb.tensor.tensor_2d.TensorNetwork2D.compress_row`.

        See Also
        --------
        contract_boundary_from_bottom, contract_boundary_from_left,
        contract_boundary_from_right
        """
        tn = self if inplace else self.copy()

        if yrange is None:
            yrange = (0, self.Ly - 1)

        if layer_tags is None:
            tn._contract_boundary_from_top_single(
                xrange, yrange, canonize=canonize,
                compress_sweep=compress_sweep, **compress_opts)
        else:
            tn._contract_boundary_from_top_multi(
                xrange, yrange, layer_tags, canonize=canonize,
                compress_sweep=compress_sweep, **compress_opts)

        return tn

    contract_boundary_from_top_ = functools.partialmethod(
        contract_boundary_from_top, inplace=True)

    def _contract_boundary_from_left_single(
        self,
        yrange,
        xrange,
        canonize=True,
        compress_sweep='up',
        layer_tag=None,
        **compress_opts
    ):
        canonize_sweep = {
            'up': 'down',
            'down': 'up',
        }[compress_sweep]

        for j in range(min(yrange), max(yrange)):
            #
            #     ●──●──       ●──
            #     │  │         ║
            #     ●──●──  ==>  ●──
            #     │  │         ║
            #     ●──●──       ●──
            #
            for i in range(min(xrange), max(xrange) + 1):
                tag1, tag2 = self.site_tag(i, j), self.site_tag(i, j + 1)
                if layer_tag is None:
                    # contract any tensors with coordinates (i, j), (i, j + 1)
                    self.contract_((tag1, tag2), which='any')
                else:
                    # contract a specific pair
                    self.contract_between(tag1, (tag2, layer_tag))
            if canonize:
                #
                #     ●──       v──
                #     ║         ║
                #     ●──  ==>  v──
                #     ║         ║
                #     ●──       ●──
                #
                self.canonize_column(j, sweep=canonize_sweep, xrange=xrange)
            #
            #     v──       ●──
            #     ║         │
            #     v──  ==>  ^──
            #     ║         │
            #     ●──       ^──
            #
            self.compress_column(j, sweep=compress_sweep,
                                 xrange=xrange, **compress_opts)

    def _contract_boundary_from_left_multi(
        self,
        yrange,
        xrange,
        layer_tags,
        canonize=True,
        compress_sweep='up',
        **compress_opts
    ):
        for j in range(min(yrange), max(yrange)):
            # make sure the exterior sites are a single tensor
            #
            #     ○──○──           ●──○──
            #     │╲ │╲            │╲ │╲       (for two layer tags)
            #     ●─○──○──         ╰─●──○──
            #      ╲│╲╲│╲     ==>    │╲╲│╲
            #       ●─○──○──         ╰─●──○──
            #        ╲│ ╲│             │ ╲│
            #         ●──●──           ╰──●──
            #
            for i in range(min(xrange), max(xrange) + 1):
                self ^= (i, j)

            for tag in layer_tags:
                # contract interior sites from layer ``tag``
                #
                #        ○──
                #      ╱╱ ╲        (first contraction if there are two tags)
                #     ●─── ○──
                #      ╲ ╱╱ ╲
                #       ^─── ○──
                #        ╲ ╱╱
                #         ^─────
                #
                self._contract_boundary_from_left_single(
                    yrange=(j, j + 1), xrange=xrange, canonize=canonize,
                    compress_sweep=compress_sweep, layer_tag=tag,
                    **compress_opts)

                # so we can still uniqely identify 'inner' tensors, drop inner
                #     site tag merged into outer tensor for all but last tensor
                for i in range(min(xrange), max(xrange) + 1):
                    inner_tag = self.site_tag(i, j + 1)
                    if len(self.tag_map[inner_tag]) > 1:
                        self[i, j].drop_tags(inner_tag)

    def contract_boundary_from_left(
        self,
        yrange,
        xrange=None,
        canonize=True,
        compress_sweep='up',
        layer_tags=None,
        inplace=False,
        **compress_opts
    ):
        """Contract a 2D tensor network inwards from the left, canonizing and
        compressing (top to bottom) along the way.

        Parameters
        ----------
        yrange : (int, int)
            The range of columns to compress (inclusive).
        xrange : (int, int) or None, optional
            The range of rows to compress (inclusive), sweeping along with
            canonization and compression. Defaults to all rows.
        canonize : bool, optional
            Whether to sweep one way with canonization before compressing.
        compress_sweep : {'up', 'down'}, optional
            Which way to perform the compression sweep, which has an effect on
            which tensors end up being canonized.
        layer_tags : None or str, optional
            If ``None``, all tensors at each coordinate pair
            ``[(i, j), (i, j + 1)]`` will be first contracted. If specified,
            then the outer tensor at ``(i, j)`` will be contracted with the
            tensor specified by ``[(i + 1, j), layer_tag]``, for each
            ``layer_tag`` in ``layer_tags``.
        inplace : bool, optional
            Whether to perform the contraction inplace or not.
        compress_opts
            Supplied to
            :meth:`~quimb.tensor.tensor_2d.TensorNetwork2D.compress_column`.

        See Also
        --------
        contract_boundary_from_bottom, contract_boundary_from_top,
        contract_boundary_from_right
        """
        tn = self if inplace else self.copy()

        if xrange is None:
            xrange = (0, self.Lx - 1)

        if layer_tags is None:
            tn._contract_boundary_from_left_single(
                yrange, xrange, canonize=canonize,
                compress_sweep=compress_sweep, **compress_opts)
        else:
            tn._contract_boundary_from_left_multi(
                yrange, xrange, layer_tags, canonize=canonize,
                compress_sweep=compress_sweep, **compress_opts)

        return tn

    contract_boundary_from_left_ = functools.partialmethod(
        contract_boundary_from_left, inplace=True)

    def _contract_boundary_from_right_single(
        self,
        yrange,
        xrange,
        canonize=True,
        compress_sweep='down',
        layer_tag=None,
        **compress_opts
    ):
        canonize_sweep = {
            'up': 'down',
            'down': 'up',
        }[compress_sweep]

        for j in range(max(yrange), min(yrange), -1):
            #
            #     ──●──●       ──●
            #       │  │         ║
            #     ──●──●  ==>  ──●
            #       │  │         ║
            #     ──●──●       ──●
            #
            for i in range(min(xrange), max(xrange) + 1):
                tag1, tag2 = self.site_tag(i, j), self.site_tag(i, j - 1)
                if layer_tag is None:
                    # contract any tensors with coordinates (i, j), (i, j - 1)
                    self.contract_((tag1, tag2), which='any')
                else:
                    # contract a specific pair
                    self.contract_between(tag1, (tag2, layer_tag))
            if canonize:
                #
                #   ──●       ──v
                #     ║         ║
                #   ──●  ==>  ──v
                #     ║         ║
                #   ──●       ──●
                #
                self.canonize_column(j, sweep=canonize_sweep, xrange=xrange)
            #
            #   ──v       ──●
            #     ║         │
            #   ──v  ==>  ──^
            #     ║         │
            #   ──●       ──^
            #
            self.compress_column(j, sweep=compress_sweep,
                                 xrange=xrange, **compress_opts)

    def _contract_boundary_from_right_multi(
        self,
        yrange,
        xrange,
        layer_tags,
        canonize=True,
        compress_sweep='down',
        **compress_opts
    ):
        for j in range(max(yrange), min(yrange), -1):
            # make sure the exterior sites are a single tensor
            #
            #         ──○──○           ──○──●
            #          ╱│ ╱│            ╱│ ╱│    (for two layer tags)
            #       ──○──○─●         ──○──●─╯
            #        ╱│╱╱│╱   ==>     ╱│╱╱│
            #     ──○──○─●         ──○──●─╯
            #       │╱ │╱            │╱ │
            #     ──●──●           ──●──╯
            #
            for i in range(min(xrange), max(xrange) + 1):
                self ^= (i, j)

            for tag in layer_tags:
                # contract interior sites from layer ``tag``
                #
                #         ──○
                #          ╱ ╲╲     (first contraction if there are two tags)
                #       ──○────v
                #        ╱ ╲╲ ╱
                #     ──○────v
                #        ╲╲ ╱
                #     ─────●
                #
                self._contract_boundary_from_right_single(
                    yrange=(j, j - 1), xrange=xrange, canonize=canonize,
                    compress_sweep=compress_sweep, layer_tag=tag,
                    **compress_opts)

                # so we can still uniqely identify 'inner' tensors, drop inner
                #     site tag merged into outer tensor for all but last tensor
                for i in range(min(xrange), max(xrange) + 1):
                    inner_tag = self.site_tag(i, j - 1)
                    if len(self.tag_map[inner_tag]) > 1:
                        self[i, j].drop_tags(inner_tag)

    def contract_boundary_from_right(
        self,
        yrange,
        xrange=None,
        canonize=True,
        compress_sweep='down',
        layer_tags=None,
        inplace=False,
        **compress_opts
    ):
        """Contract a 2D tensor network inwards from the left, canonizing and
        compressing (top to bottom) along the way.

        Parameters
        ----------
        yrange : (int, int)
            The range of columns to compress (inclusive).
        xrange : (int, int) or None, optional
            The range of rows to compress (inclusive), sweeping along with
            canonization and compression. Defaults to all rows.
        canonize : bool, optional
            Whether to sweep one way with canonization before compressing.
        compress_sweep : {'down', 'up'}, optional
            Which way to perform the compression sweep, which has an effect on
            which tensors end up being canonized.
        layer_tags : None or str, optional
            If ``None``, all tensors at each coordinate pair
            ``[(i, j), (i, j - 1)]`` will be first contracted. If specified,
            then the outer tensor at ``(i, j)`` will be contracted with the
            tensor specified by ``[(i + 1, j), layer_tag]``, for each
            ``layer_tag`` in ``layer_tags``.
        inplace : bool, optional
            Whether to perform the contraction inplace or not.
        compress_opts
            Supplied to
            :meth:`~quimb.tensor.tensor_2d.TensorNetwork2D.compress_column`.

        See Also
        --------
        contract_boundary_from_bottom, contract_boundary_from_top,
        contract_boundary_from_left
        """
        tn = self if inplace else self.copy()

        if xrange is None:
            xrange = (0, self.Lx - 1)

        if layer_tags is None:
            tn._contract_boundary_from_right_single(
                yrange, xrange, canonize=canonize,
                compress_sweep=compress_sweep, **compress_opts)
        else:
            tn._contract_boundary_from_right_multi(
                yrange, xrange, layer_tags, canonize=canonize,
                compress_sweep=compress_sweep, **compress_opts)

        return tn

    contract_boundary_from_right_ = functools.partialmethod(
        contract_boundary_from_right, inplace=True)

    def contract_boundary(
        self,
        around=None,
        layer_tags=None,
        max_separation=1,
        sequence='bltr',
        bottom=None,
        top=None,
        left=None,
        right=None,
        inplace=False,
        **boundary_contract_opts,
    ):
        """Contract the boundary of this 2D tensor network inwards::

            ●──●──●──●       ●──●──●──●       ●──●──●
            │  │  │  │       │  │  │  │       ║  │  │
            ●──●──●──●       ●──●──●──●       ^──●──●       >══>══●       >──v
            │  │ij│  │  ==>  │  │ij│  │  ==>  ║ij│  │  ==>  │ij│  │  ==>  │ij║
            ●──●──●──●       ●══<══<══<       ^──<──<       ^──<──<       ^──<
            │  │  │  │
            ●──●──●──●

        Optionally from any or all of the boundary, in multiple layers, and
        stopping around a region.

        Parameters
        ----------
        around : None or sequence of (int, int), optional
            If given, don't contract the square of sites bounding these
            coordinates.
        layer_tags : None or sequence of str, optional
            If given, perform a multilayer contraction, contracting the inner
            sites in each layer into the boundary individually.
        max_separation : int, optional
            If ``around is None``, when any two sides become this far apart
            simply contract the remaining tensor network.
        sequence : sequence of {'b', 'l', 't', 'r'}, optional
            Which directions to cycle throught when performing the inwards
            contractions: 'b', 'l', 't', 'r' corresponding to *from the*
            bottom, left, top and right respectively. If ``around`` is
            specified you will likely need all of these!
        bottom : int, optional
            The initial bottom boundary row, defaults to 0.
        top : int, optional
            The initial top boundary row, defaults to ``Lx - 1``.
        left : int, optional
            The initial left boundary column, defaults to 0.
        right : int, optional
            The initial right boundary column, defaults to ``Ly - 1``..
        inplace : bool, optional
            Whether to perform the contraction in place or not.
        boundary_contract_opts
            Supplied to
            :meth:`~quimb.tensor.tensor_2d.TensorNetwork2D.contract_boundary_from_bottom`,
            :meth:`~quimb.tensor.tensor_2d.TensorNetwork2D.contract_boundary_from_left`,
            :meth:`~quimb.tensor.tensor_2d.TensorNetwork2D.contract_boundary_from_top`,
            or
            :meth:`~quimb.tensor.tensor_2d.TensorNetwork2D.contract_boundary_from_right`,
            including compression and canonization options.
        """
        tn = self if inplace else self.copy()

        boundary_contract_opts['layer_tags'] = layer_tags

        # set default starting borders
        if bottom is None:
            bottom = 0
        if top is None:
            top = tn.Lx - 1
        if left is None:
            left = 0
        if right is None:
            right = tn.Ly - 1

        if around is not None:
            stop_i_min = min(x[0] for x in around)
            stop_i_max = max(x[0] for x in around)
            stop_j_min = min(x[1] for x in around)
            stop_j_max = max(x[1] for x in around)

        # keep track of whether we have hit the ``around`` region.
        reached_stop = {direction: False for direction in sequence}

        for direction in cycle(sequence):

            if direction == 'b':
                # for each direction check if we have reached the 'stop' region
                if (around is None) or (bottom + 1 < stop_i_min):
                    tn.contract_boundary_from_bottom_(
                        xrange=(bottom, bottom + 1),
                        yrange=(left, right),
                        compress_sweep='left',
                        **boundary_contract_opts,
                    )
                    bottom += 1
                else:
                    reached_stop[direction] = True

            elif direction == 'l':
                if (around is None) or (left + 1 < stop_j_min):
                    tn.contract_boundary_from_left_(
                        xrange=(bottom, top),
                        yrange=(left, left + 1),
                        compress_sweep='up',
                        **boundary_contract_opts
                    )
                    left += 1
                else:
                    reached_stop[direction] = True

            elif direction == 't':
                if (around is None) or (top - 1 > stop_i_max):
                    tn.contract_boundary_from_top_(
                        xrange=(top, top - 1),
                        compress_sweep='right',
                        yrange=(left, right),
                        **boundary_contract_opts
                    )
                    top -= 1
                else:
                    reached_stop[direction] = True

            elif direction == 'r':
                if (around is None) or (right - 1 > stop_j_max):
                    tn.contract_boundary_from_right_(
                        xrange=(bottom, top),
                        yrange=(right, right - 1),
                        compress_sweep='down',
                        **boundary_contract_opts
                    )
                    right -= 1
                else:
                    reached_stop[direction] = True

            else:
                raise ValueError("'sequence' should be an iterable of "
                                 "'b', 'l', 't', 'r' only.")

            if around is None:
                # check if TN has become thin enough to just contract
                thin_strip = (
                    (top - bottom <= max_separation) or
                    (right - left <= max_separation)
                )
                if thin_strip:
                    return tn.contract(all, optimize='auto-hq')

            # check if all directions have reached the ``around`` region
            elif all(reached_stop.values()):
                break

        return tn

    contract_boundary_ = functools.partialmethod(
        contract_boundary, inplace=True)


class TensorNetwork2DVector(TensorNetwork2D,
                            TensorNetwork):
    """Mixin class  for a 2D square lattice vector TN, i.e. one with a single
    physical index per site.
    """

    _EXTRA_PROPS = (
        '_site_tag_id',
        '_row_tag_id',
        '_col_tag_id',
        '_Lx',
        '_Ly',
        '_site_ind_id',
    )

    @property
    def site_ind_id(self):
        return self._site_ind_id

    def site_ind(self, i, j):
        if not isinstance(i, str):
            i = i % self.Lx
        if not isinstance(j, str):
            j = j % self.Ly
        return self.site_ind_id.format(i, j)

    def reindex_sites(self, new_id, where=None, inplace=False):
        if where is None:
            where = self.gen_site_coos()

        return self.reindex(
            {
                self.site_ind(*ij): new_id.format(*ij) for ij in where
            },
            inplace=inplace
        )

    @site_ind_id.setter
    def site_ind_id(self, new_id):
        if self._site_ind_id != new_id:
            self.reindex_sites(new_id, inplace=True)
            self._site_ind_id = new_id

    @property
    def site_inds(self):
        """All of the site inds.
        """
        return tuple(self.site_ind(i, j) for i, j in self.gen_site_coos())

    def to_dense(self, *inds_seq, **contract_opts):
        """Return the dense ket version of this 2D vector, i.e. a ``qarray``
        with shape (-1, 1).
        """
        if not inds_seq:
            # just use list of site indices
            return do('reshape', TensorNetwork.to_dense(
                self, self.site_inds, **contract_opts
            ), (-1, 1))

        return TensorNetwork.to_dense(self, *inds_seq, **contract_opts)

    def phys_dim(self, i=0, j=0):
        """Get the size of the physical indices / a specific physical index.
        """
        return self.ind_size(self.site_ind(i, j))


class TensorNetwork2DOperator(TensorNetwork2D,
                              TensorNetwork):
    """Mixin class  for a 2D square lattice TN operator, i.e. one with both
    'upper' and 'lower' site (physical) indices.
    """

    _EXTRA_PROPS = (
        '_site_tag_id',
        '_row_tag_id',
        '_col_tag_id',
        '_Lx',
        '_Ly',
        '_upper_ind_id',
        '_lower_ind_id',
    )

    @property
    def lower_ind_id(self):
        return self._lower_ind_id

    def lower_ind(self, i, j):
        if not isinstance(i, str):
            i = i % self.Lx
        if not isinstance(j, str):
            j = j % self.Ly
        return self.lower_ind_id.format(i, j)

    @property
    def lower_inds(self):
        """All of the lower inds.
        """
        return tuple(self.lower_ind(i, j) for i, j in self.gen_site_coos())

    @property
    def upper_ind_id(self):
        return self._upper_ind_id

    def upper_ind(self, i, j):
        if not isinstance(i, str):
            i = i % self.Lx
        if not isinstance(j, str):
            j = j % self.Ly
        return self.upper_ind_id.format(i, j)

    @property
    def upper_inds(self):
        """All of the upper inds.
        """
        return tuple(self.upper_ind(i, j) for i, j in self.gen_site_coos())

    def to_dense(self, *inds_seq, **contract_opts):
        """Return the dense matrix version of this 2D operator, i.e. a
        ``qarray`` with shape (d, d).
        """
        if not inds_seq:
            inds_seq = (self.upper_inds, self.lower_inds)

        return TensorNetwork.to_dense(self, *inds_seq, **contract_opts)

    def phys_dim(self, i=0, j=0, which='upper'):
        """Get a physical index size of this 2D operator.
        """
        if which == 'upper':
            return self[i, j].ind_size(self.upper_ind(i))

        if which == 'lower':
            return self[i, j].ind_size(self.lower_ind(i))


class TensorNetwork2DFlat(TensorNetwork2D,
                          TensorNetwork):
    """Mixin class for a 2D square lattice tensor network with a single tensor
    per site, for example, both PEPS and PEPOs.
    """

    _EXTRA_PROPS = (
        '_site_tag_id',
        '_row_tag_id',
        '_col_tag_id',
        '_Lx',
        '_Ly',
    )

    def bond(self, coo1, coo2):
        """Get the name of the index defining the bond between sites at
        ``coo1`` and ``coo2``.
        """
        b_ix, = self[coo1].bonds(self[coo2])
        return b_ix

    def bond_size(self, coo1, coo2):
        """Return the size of the bond between sites at ``coo1`` and ``coo2``.
        """
        b_ix = self.bond(coo1, coo2)
        return self[coo1].ind_size(b_ix)


class PEPS(TensorNetwork2DVector,
           TensorNetwork2DFlat,
           TensorNetwork2D,
           TensorNetwork):
    r"""Projected Entangled Pair States object::


                         ...
             │    │    │    │    │    │
             ●────●────●────●────●────●──
            ╱│   ╱│   ╱│   ╱│   ╱│   ╱│
             │    │    │    │    │    │
             ●────●────●────●────●────●──
            ╱│   ╱│   ╱│   ╱│   ╱│   ╱│
             │    │    │    │    │    │   ...
             ●────●────●────●────●────●──
            ╱│   ╱│   ╱│   ╱│   ╱│   ╱│
             │    │    │    │    │    │
             ●────●────●────●────●────●──
            ╱    ╱    ╱    ╱    ╱    ╱

    Parameters
    ----------
    arrays : sequence of sequence of array
        The core tensor data arrays.
    shape : str, optional
        Which order the dimensions of the arrays are stored in, the default
        ``'urdlp'`` stands for ('up', 'right', 'down', 'left', 'physical').
        Arrays on the edge of lattice are assumed to be missing the
        corresponding dimension.
    tags : set[str], optional
        Extra global tags to add to the tensor network.
    site_ind_id : str, optional
        String specifier for naming convention of site indices.
    site_tag_id : str, optional
        String specifier for naming convention of site tags.
    row_tag_id : str, optional
        String specifier for naming convention of row tags.
    col_tag_id : str, optional
        String specifier for naming convention of column tags.
    """

    _EXTRA_PROPS = (
        '_site_tag_id',
        '_row_tag_id',
        '_col_tag_id',
        '_Lx',
        '_Ly',
        '_site_ind_id',
    )

    def __init__(self, arrays, *, shape='urdlp', tags=None,
                 site_ind_id='k{},{}', site_tag_id='I{},{}',
                 row_tag_id='ROW{}', col_tag_id='COL{}', **tn_opts):

        if isinstance(arrays, PEPS):
            super().__init__(arrays)
            return

        tags = tags_to_utup(tags)
        self._site_ind_id = site_ind_id
        self._site_tag_id = site_tag_id
        self._row_tag_id = row_tag_id
        self._col_tag_id = col_tag_id

        arrays = tuple(tuple(x for x in xs) for xs in arrays)
        self._Lx = len(arrays)
        self._Ly = len(arrays[0])
        tensors = []

        # cache for both creating and retrieving indices
        ix = defaultdict(rand_uuid)

        for i, j in product(range(self.Lx), range(self.Ly)):
            array = arrays[i][j]

            # figure out if we need to transpose the arrays from some order
            #     other than up right down left physical
            array_order = shape
            if i == self.Lx - 1:
                array_order = array_order.replace('u', '')
            if j == self.Ly - 1:
                array_order = array_order.replace('r', '')
            if i == 0:
                array_order = array_order.replace('d', '')
            if j == 0:
                array_order = array_order.replace('l', '')

            # allow convention of missing bonds to be singlet dimensions
            if len(array.shape) != len(array_order):
                array = do('squeeze', array)

            transpose_order = tuple(
                array_order.find(x) for x in 'urdlp' if x in array_order
            )
            if transpose_order != tuple(range(len(array_order))):
                array = do('transpose', array, transpose_order)

            # get the relevant indices corresponding to neighbours
            inds = []
            if 'u' in array_order:
                inds.append(ix[(i + 1, j), (i, j)])
            if 'r' in array_order:
                inds.append(ix[(i, j), (i, j + 1)])
            if 'd' in array_order:
                inds.append(ix[(i, j), (i - 1, j)])
            if 'l' in array_order:
                inds.append(ix[(i, j - 1), (i, j)])
            inds.append(self.site_ind(i, j))

            # mix site, row, column and global tags
            ij_tags = utup_union((tags, (self.site_tag(i, j),
                                         self.row_tag(i),
                                         self.col_tag(j))))

            # create the site tensor!
            tensors.append(Tensor(data=array, inds=inds, tags=ij_tags))

        super().__init__(tensors, check_collisions=False, **tn_opts)

    @classmethod
    def rand(cls, Lx, Ly, bond_dim, phys_dim=2,
             dtype=float, seed=None, **peps_opts):
        """Create a random (un-normalized) PEPS.

        Parameters
        ----------
        Lx : int
            The number of rows.
        Ly : int
            The number of columns.
        bond_dim : int
            The bond dimension.
        physical : int, optional
            The physical index dimension.
        dtype : dtype, optional
            The dtype to create the arrays with, default is real double.
        seed : int, optional
            A random seed.
        peps_opts
            Supplied to :class:`~quimb.tensor.tensor_2d.PEPS`.

        Returns
        -------
        psi : PEPS
        """
        if seed is not None:
            seed_rand(seed)

        arrays = [[None for _ in range(Ly)] for _ in range(Lx)]

        for i, j in product(range(Lx), range(Ly)):

            shape = []
            if i != Lx - 1:  # bond up
                shape.append(bond_dim)
            if j != Ly - 1:  # bond right
                shape.append(bond_dim)
            if i != 0:  # bond down
                shape.append(bond_dim)
            if j != 0:  # bond left
                shape.append(bond_dim)
            shape.append(phys_dim)

            arrays[i][j] = sensibly_scale(sensibly_scale(
                randn(shape, dtype=dtype)))

        return cls(arrays, **peps_opts)

    def show(self):
        """Print a unicode schematic of this PEPS and its bond dimensions.
        """
        show_2d(self, show_lower=True)


def gen_2d_bond_pairs(Lx, Ly, cyclic=(False, False)):
    r"""Utility for generating all the bond coordinates for a grid::

                 ...

          │       │       │       │
          │       │       │       │
        (1,0)───(1,1)───(1,2)───(1,3)───
          │       │       │       │
          │       │       │       │
        (1,0)───(1,1)───(1,2)───(1,3)───  ...
          │       │       │       │
          │       │       │       │
        (0,0)───(0,1)───(0,2)───(0,3)───

    Including with cyclic boundary conditions in either or both directions.
    """
    for i in range(Lx):
        for j in range(Ly):
            if i + 1 < Lx:
                yield (i, j), (i + 1, j)
            if j + 1 < Ly:
                yield (i, j), (i, j + 1)

            if cyclic[0] and i == Lx - 1:
                yield (0, j), (i, j)

            if cyclic[1] and j == Ly - 1:
                yield (i, 0), (i, j)


def show_2d(tn_2d, show_lower=False, show_upper=False):
    """Base function for printing a unicode schematic of flat 2D TNs.
    """

    lb = '╱' if show_lower else ' '
    ub = '╱' if show_upper else ' '

    line0 = ' ' + (f' {ub}{{:^3}}' * (tn_2d.Ly - 1)) + f' {ub}'
    bszs = [tn_2d.bond_size((0, j), (0, j + 1)) for j in range(tn_2d.Ly - 1)]

    lines = [line0.format(*bszs)]

    for i in range(tn_2d.Lx - 1):
        lines.append(' ●' + ('━━━━●' * (tn_2d.Ly - 1)))

        # vertical bonds
        lines.append(f'{lb}┃{{:<3}}' * tn_2d.Ly)
        bszs = [tn_2d.bond_size((i, j), (i + 1, j)) for j in range(tn_2d.Ly)]
        lines[-1] = lines[-1].format(*bszs)

        # horizontal bonds below
        lines.append(' ┃' + (f'{ub}{{:^3}}┃' * (tn_2d.Ly - 1)) + f'{ub}')
        bszs = [tn_2d.bond_size((i + 1, j), (i + 1, j + 1))
                for j in range(tn_2d.Ly - 1)]
        lines[-1] = lines[-1].format(*bszs)

    lines.append(' ●' + ('━━━━●' * (tn_2d.Ly - 1)))
    lines.append(f'{lb}    ' * tn_2d.Ly)

    print_multi_line(*lines)