# coding: utf-8
# Copyright (c) Max-Planck-Institut für Eisenforschung GmbH - Computational Materials Design (CM) Department
# Distributed under the terms of "New BSD License", see the LICENSE file.

from __future__ import division, print_function
from copy import copy
from collections import OrderedDict
from math import cos, sin
import numpy as np
from six import string_types
from ase.geometry import cellpar_to_cell, complete_cell

from pyiron_atomistics.structure.atom import Atom
from pyiron_atomistics.structure.sparse_list import SparseArray, SparseList
from pyiron_atomistics.structure.periodic_table import PeriodicTable, ChemicalElement, ElementColorDictionary
from pyiron_base.core.settings.generic import Settings

try:
    from scipy.spatial import cKDTree
except ImportError:
    from scipy.spatial import ckdtree

try:
    import spglib
except ImportError:
    try:
        import pyspglib as spglib
    except ImportError:
        pass

__author__ = "Joerg Neugebauer, Sudarsan Surendralal"
__copyright__ = "Copyright 2017, Max-Planck-Institut für Eisenforschung GmbH - Computational Materials Design (CM) Department"
__version__ = "1.0"
__maintainer__ = "Sudarsan Surendralal"
__email__ = "surendralal@mpie.de"
__status__ = "production"
__date__ = "Sep 1, 2017"

s = Settings()


class Atoms(object):
    """
    The Atoms class represents all the information required to describe a structure at the atomic scale. This class is
    written in such a way that is compatible with the ase atoms class.

    Args:
        elements (list/numpy.ndarray instance): List of strings containing the elements or a list of
                            pyiron_atomistics.structure.periodic_table.ChemicalElement instances
        numbers (list/numpy.ndarray instance): List of atomic numbers of elements
        symbols (list/numpy.ndarray instance): List of chemical symbols
        positions (list/numpy.ndarray): List of positions
        scaled_positions (list/numpy.ndarray instance): List of scaled positions (relative coordinates)
        pbc (boolean): Tells if periodic boundary conditions should be applied
        cell (list/numpy.ndarray instance): A 3x3 array representing the lattice vectors of the structure
        is_absolute (boolean): Tells if the specified positions are in absolute coordinates

    Note: Only one of elements/symbols or numbers should be assigned during initialization

    Attributes:

        positions (numpy.ndarray): A size Nx3 positions of the structure which has N ions. They are in absolute or
                                relative coordinates based on the is_absolute tag.
        cell (numpy.ndarray): A size 3x3 array which gives the lattice vectors of the cell as [a1, a2, a3]

    """
    def __init__(self, symbols=None, positions=None, numbers=None, tags=None, momenta=None, masses=None,
                 magmoms=None, charges=None, scaled_positions=None, cell=None, pbc=None, celldisp=None, constraint=None,
                 calculator=None, info=None, indices=None, elements=None, dimension=None, species=None,
                 **qwargs):
        # print ('init atoms: ', scaled_positions, cell, qwargs)
        if symbols is not None:
            if elements is None:
                elements = symbols
            else:
                raise ValueError("Only elements OR symbols should be given.")
        if tags is not None or momenta is not None or masses is not None or charges is not None \
                or celldisp is not None or constraint is not None or calculator is not None or info is not None:
            s.logger.debug('Not supported parameter used!')
        self._store_elements = dict()
        self._species_to_index_dict = None
        self.colorLut = ElementColorDictionary().to_lut()
        self._is_scaled = False
        if cell is not None:
            # make it ASE compatible
            if np.linalg.matrix_rank(cell) == 1:
                cell = np.eye(len(cell)) * cell
        self.cell = cell
        self._internal_positions = None
        self._pse = PeriodicTable()
        self._tag_list = SparseArray()
        self.indices = np.array([])
        element_list = None

        if (elements is None) and (numbers is None) and (indices is None):
            return
        if numbers is not None:  # for ASE compatibility
            assert (elements is None)
            elements = self.numbers_to_elements(numbers)
        if elements is not None:
            # assert (numbers is None)
            el_object_list = None
            if isinstance(elements, str):
                element_list = self.convert_formula(elements)
            elif isinstance(elements, (list, tuple, np.ndarray)):
                is_mixed = False
                init_type = type(elements[0])
                for el in elements:
                    if type(el) == init_type:
                        pass
                    else:
                        is_mixed = True
                        break
                if is_mixed:
                    object_list = list()
                    for el in elements:
                        # print("Type: {}".format(type(el)) )
                        if isinstance(el, (str, np.str, np.str_)):
                            object_list.append(self.convert_element(el))
                        if isinstance(el, ChemicalElement):
                            object_list.append(el)
                        if isinstance(el, Atom):
                            object_list.append(el.element)
                        if isinstance(el, (int, np.int64, np.int32)):
                            # pse = PeriodicTable()
                            object_list.append(self._pse.element(el))
                        el_object_list = object_list

                if len(elements) == 0:
                    element_list = elements
                else:
                    if isinstance(elements[0], (list, tuple, np.ndarray)):
                        elements = np.array(elements).flatten()

                    if isinstance(elements[0], string_types):
                        element_list = elements
                    elif isinstance(elements[0], ChemicalElement):
                        el_object_list = elements
                    elif isinstance(elements[0], Atom):
                        el_object_list = [el.element for el in elements]
                        positions = [el.position for el in elements]
                    elif elements.dtype in [int, np.int64, np.int32]:
                        el_object_list = self.numbers_to_elements(elements)
                    else:
                        print("elementList: ", elements, type(elements[0]))
                        raise ValueError('Unknown static type for element in list: ' + str(type(elements[0])))

            if el_object_list is None:
                el_object_list = [self.convert_element(el) for el in element_list]

            self.set_species(list(set(el_object_list)))
            # species_to_index_dict = {el: i for i, el in enumerate(self.species)}
            el_index_lst = [self._species_to_index_dict[el] for el in el_object_list]

        elif indices is not None:
            el_index_lst = indices
            self.set_species(species)

        if scaled_positions is not None:
            if positions is not None:
                raise ValueError("either position or scaled_positions can be given")
            if cell is None:
                raise ValueError('scaled_positions can only be used with a given cell')
            positions = np.dot(np.array(cell).T, np.array(scaled_positions).T).T

        if positions is None:
            self.dimension = 3
            if cell is not None:
                positions = np.zeros((len(el_index_lst), self.dimension))

        self.indices = np.array(el_index_lst)
        self.positions = np.array(positions).astype(np.float)
        self._tag_list._length = len(positions)

        for key, val in qwargs.items():
            print ('set qwargs (ASE): ', key, val)
            setattr(self, key, val)

        self.units = {"length": "A", "mass": "u"}
        if len(positions) > 0:
            self.dimension = len(positions[0])
        else:
            self.dimension = 3
        if dimension is not None:
            self.dimension = dimension
        self.adsorbate_info = {}
        self.bonds = None
        self.pbc = False
        if cell is not None:
            if pbc is None:
                self.pbc = True  # default setting
            else:
                self.pbc = pbc
        self._info = dict()
        self.arrays = dict()
        self.set_initial_magnetic_moments(magmoms)

    @property
    def cell(self):
        """

        Returns:

        """
        return self._cell

    @cell.setter
    def cell(self, value):
        """

        Args:
            value:

        Returns:

        """
        if value is None:
            self._cell = None
        else:
            if self._is_scaled:
                self.set_cell(value, scale_atoms=True)
            else:
                self.set_cell(value)

    @property
    def scaled_positions(self):
        if self._is_scaled:
            return self._internal_positions
        else:
            b_mat = np.linalg.inv(self.cell)
            return np.dot(b_mat.T, np.array(self.positions).T).T

    @property
    def positions(self):
        if self._is_scaled:
            return np.dot(self.cell.T, np.array(self._internal_positions).T).T
        else:
            return self._internal_positions

    @scaled_positions.setter
    def scaled_positions(self, positions):
        if self._is_scaled:
            self._internal_positions = positions
        else:
            self._internal_positions = np.dot(self.cell.T, np.array(positions).T).T

    @positions.setter
    def positions(self, positions):
        if self._is_scaled:
            b_mat = np.linalg.inv(self.cell)
            self._internal_positions = np.dot(b_mat.T, np.array(positions).T).T
        else:
            self._internal_positions = positions

    @property
    def species(self):
        return self._species

    # @species.setter
    def set_species(self, value):
        if value is None:
            return
        value = list(value)
        self._species_to_index_dict = {el: i for i, el in enumerate(value)}
        self._species = value[:]
        self._store_elements = {el.Abbreviation: el for el in value}

    @property
    def info(self):
        """

        Returns:

        """
        return self._info

    @info.setter
    def info(self, val):
        """

        Args:
            val:

        Returns:

        """
        self._info = val

    @property
    def pbc(self):
        """

        Returns:

        """
        if not isinstance(self._pbc, np.ndarray):
            self.set_pbc(self._pbc)
        return self._pbc

    @pbc.setter
    def pbc(self, val):
        self._pbc = val

    @property
    def elements(self):
        return np.array([self.species[el] for el in self.indices])

    def new_array(self, name, a, dtype=None, shape=None):
        """Add new array.

        If *shape* is not *None*, the shape of *a* will be checked."""

        if dtype is not None:
            a = np.array(a, dtype, order='C')
            if len(a) == 0 and shape is not None:
                a.shape = (-1,) + shape
        else:
            if not a.flags['C_CONTIGUOUS']:
                a = np.ascontiguousarray(a)
            else:
                a = a.copy()

        if name in self.arrays:
            raise RuntimeError

        for b in self.arrays.values():
            if len(a) != len(b):
                raise ValueError('Array has wrong length: %d != %d.' %
                                 (len(a), len(b)))
            break

        if shape is not None and a.shape[1:] != shape:
            raise ValueError('Array has wrong shape %s != %s.' %
                             (a.shape, (a.shape[0:1] + shape)))

        self.arrays[name] = a

    def get_array(self, name, copy=True):
        """Get an array.

        Returns a copy unless the optional argument copy is false.
        """
        if copy:
            return self.arrays[name].copy()
        else:
            return self.arrays[name]

    def set_array(self, name, a, dtype=None, shape=None):
        """Update array.

        If *shape* is not *None*, the shape of *a* will be checked.
        If *a* is *None*, then the array is deleted."""

        b = self.arrays.get(name)
        if b is None:
            if a is not None:
                self.new_array(name, a, dtype, shape)
        else:
            if a is None:
                del self.arrays[name]
            else:
                a = np.asarray(a)
                if a.shape != b.shape:
                    raise ValueError('Array has wrong shape %s != %s.' %
                                     (a.shape, b.shape))
                b[:] = a

    def add_tag(self, *args, **qwargs):
        self._tag_list.add_tag(*args, **qwargs)

    # @staticmethod
    def numbers_to_elements(self, numbers):
        """
        Convert atomic numbers in element objects (needed for compatibility with ASE)

        Args:
            numbers (list): List of Element Numbers (as Integers; default in ASE)

        Returns:
            elements: list of elements as needed for pyiron

        """
        # pse = PeriodicTable()  # TODO; extend to internal PSE which can contain additional elements and tags
        atom_number_to_element = {}
        for i_el in set(numbers):
            i_el = int(i_el)
            atom_number_to_element[i_el] = self._pse.element(i_el)
        return [atom_number_to_element[i_el] for i_el in numbers]

    def copy(self):
        return self.__copy__()

    def to_hdf(self, hdf, group_name="structure"):
        """

        Args:
            hdf: 
            group_name: 

        Returns:

        """
        # import time
        with hdf.open(group_name) as hdf_structure:
            # time_start = time.time()
            hdf_structure["TYPE"] = str(type(self))
            for el in self.species:
                if isinstance(el.tags, dict):
                    with hdf_structure.open("new_species") as hdf_species:
                    # species_lst = set(self.elements)
                        el.to_hdf(hdf_species)
            # species = [el.Abbreviation for el in species_lst]
            # species_dict = {key:i for i, key in enumerate(species)}
            # element_lst = np.array([species_dict[el] for el in self.get_chemical_symbols()])
            hdf_structure['species'] = [el.Abbreviation for el in self.species]
            # print('time in atoms.to_hdf (species): ', time.time() - time_start)
            # H5py Python3 unicode issue: https://github.com/h5py/h5py/issues/289
            # hdf_structure["elements"] = np.array(self.get_chemical_symbols())
            # hdf_structure["elements"] = self.get_chemical_symbols()
            hdf_structure["indices"] = self.indices
            # print('time in atoms.to_hdf (structure): ', time.time() - time_start)

            with hdf_structure.open("tags") as hdf_tags:
                for tag in self._tag_list.keys():
                    tag_value = self._tag_list[tag]
                    if isinstance(tag_value, SparseList):
                        tag_value.to_hdf(hdf_tags, tag)

            # print('time in atoms.to_hdf(tags): ', time.time() - time_start)
            # tr_dict = {True: "True", False: "False"}
            hdf_structure["units"] = self.units
            hdf_structure["dimension"] = self.dimension

            if self.cell is not None:
                with hdf_structure.open("cell") as hdf_cell:
                    hdf_cell["cell"] = self.cell
                    hdf_cell["pbc"] = self.pbc

            hdf_structure["coordinates"] = self.positions  # "Atomic coordinates"

            # potentials with explicit bonds (TIP3P, harmonic, etc.)
            if self.bonds is not None:
                hdf_structure["explicit_bonds"] = self.bonds

            # print ('time in atoms.to_hdf: ', time.time() - time_start)

    def from_hdf(self, hdf, group_name="structure"):
        """
        
        Args:
            hdf: 
            group_name: 

        Returns:

        """
        if "indices" in hdf[group_name].list_nodes():
            with hdf.open(group_name) as hdf_atoms:
                if "new_species" in hdf_atoms.list_groups():
                    with hdf_atoms.open("new_species") as hdf_species:
                        # print("hdf(species: ", hdf._h5_group)
                        self._pse.from_hdf(hdf_species)

                el_object_list = [self.convert_element(el, self._pse) for el in hdf_atoms["species"]]
                self.indices = hdf_atoms["indices"]
                self._tag_list._length = len(self)

                self.set_species(el_object_list)
                self.bonds = None
                if "explicit_bonds" in hdf_atoms.list_nodes():
                    # print "bonds: "
                    self.bonds = hdf_atoms["explicit_bonds"]

                if "tags" in hdf_atoms.list_groups():
                    with hdf_atoms.open("tags") as hdf_tags:
                        tags = hdf_tags.list_nodes()
                        for tag in tags:
                            # tr_dict = {'0': False, '1': True}
                            if isinstance(hdf_tags[tag], (list, np.ndarray)):
                                my_list = hdf_tags[tag]
                                self._tag_list[tag] = SparseList(my_list, length=len(self))

                            else:
                                my_dict = hdf_tags.get_pandas(tag).to_dict()
                                my_dict = {i: val for i, val in zip(my_dict["index"], my_dict["values"])}
                                self._tag_list[tag] = SparseList(my_dict, length=len(self))

                tr_dict = {1: True, 0: False}
                self.dimension = hdf_atoms["dimension"]
                self.units = hdf_atoms["units"]

                self.cell = None
                if "cell" in hdf_atoms.list_groups():
                    with hdf_atoms.open("cell") as hdf_cell:
                        self.cell = hdf_cell["cell"]
                        self.pbc = hdf_cell["pbc"]

                # Backward compatibility
                if "is_absolute" in hdf_atoms.list_nodes():
                    if not tr_dict[hdf_atoms["is_absolute"]]:
                        self.scaled_positions = hdf_atoms["coordinates"]
                    else:
                        self.positions = hdf_atoms["coordinates"]
                else:
                    self.positions = hdf_atoms["coordinates"]

                if "bonds" in hdf_atoms.list_nodes():
                    self.bonds = hdf_atoms["explicit_bonds"]
                return self

        else:
            return self.from_hdf_old(hdf, group_name)

    def from_hdf_old(self, hdf, group_name="structure"):
        """

        Args:
            hdf:
            group_name:

        Returns:

        """
        with hdf.open(group_name) as hdf_atoms:
            self._pse = PeriodicTable()
            if "species" in hdf_atoms.list_groups():
                with hdf_atoms.open("species") as hdf_species:
                    # print("hdf(species: ", hdf._h5_group)
                    self._pse.from_hdf(hdf_species)
            chemical_symbols = np.array(hdf_atoms["elements"], dtype=str)
            el_object_list = [self.convert_element(el, self._pse) for el in chemical_symbols]
            self.set_species(list(set(el_object_list)))
            self.indices = [self._species_to_index_dict[el] for el in el_object_list]
            self._tag_list._length = len(self)
            self.bonds = None
            if "explicit_bonds" in hdf_atoms.list_nodes():
                # print "bonds: "
                self.bonds = hdf_atoms["explicit_bonds"]

            if "tags" in hdf_atoms.list_groups():
                with hdf_atoms.open("tags") as hdf_tags:
                    tags = hdf_tags.list_nodes()
                    for tag in tags:
                        # tr_dict = {'0': False, '1': True}
                        if isinstance(hdf_tags[tag], (list, np.ndarray)):
                            my_list = hdf_tags[tag]
                            self._tag_list[tag] = SparseList(my_list, length=len(self))

                        else:
                            my_dict = hdf_tags.get_pandas(tag).to_dict()
                            my_dict = {i: val for i, val in zip(my_dict["index"], my_dict["values"])}
                            self._tag_list[tag] = SparseList(my_dict, length=len(self))

            tr_dict = {1: True, 0: False}
            self.dimension = hdf_atoms["dimension"]
            if "is_absolute" in hdf_atoms and not tr_dict[hdf_atoms["is_absolute"]]:
                self.positions = hdf_atoms["coordinates"]
            else:
                self.scaled_positions = hdf_atoms["coordinates"]
            self.units = hdf_atoms["units"]

            self.cell = None
            if "cell" in hdf_atoms.list_groups():
                with hdf_atoms.open("cell") as hdf_cell:
                    self.cell = hdf_cell["cell"]
                    self.pbc = hdf_cell["pbc"]

            if "bonds" in hdf_atoms.list_nodes():
                self.bonds = hdf_atoms["explicit_bonds"]
            return self

    def center(self, vacuum=None, axis=(0, 1, 2)):
        """
        Adopted from ASE code (https://wiki.fysik.dtu.dk/ase/_modules/ase/atoms.html#Atoms.center)
        Center atoms in unit cell.

        Centers the atoms in the unit cell, so there is the same
        amount of vacuum on all sides.

        vacuum: float (default: None)
            If specified adjust the amount of vacuum when centering.
            If vacuum=10.0 there will thus be 10 Angstrom of vacuum
            on each side.
        axis: int or sequence of ints
            Axis or axes to act on.  Default: Act on all axes.
        """
        # Find the orientations of the faces of the unit cell
        c = self.cell
        if c is None:
            c = np.identity(self.dimension)
            self.cell = c

        dirs = np.zeros_like(c)
        for i in range(3):
            dirs[i] = np.cross(c[i - 1], c[i - 2])
            dirs[i] /= np.linalg.norm(dirs[i])  # normalize
            if np.dot(dirs[i], c[i]) < 0.0:
                dirs[i] *= -1

        # Now, decide how much each basis vector should be made longer
        if isinstance(axis, int):
            axes = (axis,)
        else:
            axes = axis
        p = self.positions
        longer = np.zeros(3)
        shift = np.zeros(3)
        for i in axes:
            p0 = np.dot(p, dirs[i]).min()
            p1 = np.dot(p, dirs[i]).max()
            height = np.dot(c[i], dirs[i])
            if vacuum is not None:
                lng = (p1 - p0 + 2 * vacuum) - height
            else:
                lng = 0.0  # Do not change unit cell size!
            top = lng + height - p1
            shf = 0.5 * (top - p0)
            cosphi = np.dot(c[i], dirs[i]) / np.linalg.norm(c[i])
            longer[i] = lng / cosphi
            shift[i] = shf / cosphi

        # Now, do it!
        translation = np.zeros(3)
        for i in axes:
            nowlen = np.sqrt(np.dot(c[i], c[i]))
            self.cell[i] *= 1 + longer[i] / nowlen
            translation += shift[i] * c[i] / nowlen
        self.positions += translation
        if self.pbc is None:
            self.pbc = self.dimension * [True]

    def set_positions(self, positions):
        """
        
        Args:
            positions: 

        Returns:

        """
        self.positions = positions
        self._tag_list._length = len(self)

    def get_positions(self):
        """
        
        Returns:

        """
        return self.positions

    def select_index(self, el):
        """
        
        Args:
            el: 

        Returns:

        """
        if isinstance(el, str):
            return np.array([i for i, e in enumerate(self.get_chemical_symbols()) if e == el], dtype=int)
        elif isinstance(el, ChemicalElement):
            return np.array([i for i, e in enumerate(self.get_chemical_elements()) if e == el], dtype=int)

    def select_parent_index(self, el):
        """

        Args:
            el:

        Returns:

        """
        parent_basis = self.get_parent_basis()
        return parent_basis.select_index(el)

    def get_tags(self):
        """

        Returns:

        """
        return self._tag_list.keys()

    def get_pbc(self):
        """

        Returns:

        """
        if not isinstance(self._pbc, np.ndarray):
            self.set_pbc(self._pbc)
        return np.array(self._pbc, bool)

    def set_pbc(self, value):
        """
        
        Args:
            value: 

        Returns:

        """
        if value is None:
            self._pbc = None
        else:
            if isinstance(value, np.ndarray):
                self._pbc = value
            elif value in (True, False):
                value = self.dimension * [value]
            assert (np.shape(np.array(value)) == (self.dimension,))
            self._pbc = np.array(value, bool)

    def _return_element(self, **qwargs):
        """
        
        Args:
            **qwargs: 

        Returns:

        """
        # has to be adopted for derived class
        element = qwargs['indices']
        # print ('element: ', element)
        qwargs['element'] = self.species[element]
        qwargs['position'] = qwargs['positions']
        del qwargs['indices']
        del qwargs['positions']
        return Atom(**qwargs)

    def convert_element(self, el, pse=None):
        """
        
        Args:
            el: 
            pse: 

        Returns:

        """
        if el in list(self._store_elements.keys()):
            return self._store_elements[el]

        if isinstance(el, string_types):  # as symbol
            element = Atom(el, pse=pse).element
        elif isinstance(el, Atom):
            element = el.element
            el = el.element.Abbreviation
        elif isinstance(el, ChemicalElement):
            element = el
            el = el.Abbreviation
        else:
            raise ValueError('Unknown static type to specify a element')

        self._store_elements[el] = element
        if hasattr(self, 'species'):
            if element not in self.species:
                self._species.append(element)
                self.set_species(self._species)
        return element

    def get_chemical_formula(self):
        """
        return chemical formula of structure
        
        Returns:

        """
        species = self.get_number_species_atoms()
        formula = ""
        for s, num in species.items():
            if num == 1:
                formula += str(s)
            else:
                formula += str(s) + str(num)
        return formula

    def get_chemical_indices(self):
        return self.indices

    def get_atomic_numbers(self):
        """

        Returns:

        """
        el_lst = [el.AtomicNumber for el in self.species]
        return np.array([el_lst[el] for el in self.indices])

    def get_chemical_symbols(self):
        """

        Returns:

        """
        el_lst = [el.Abbreviation for el in self.species]
        return np.array([el_lst[el] for el in self.indices])

    def get_parent_elements(self):
        """
        
        Returns:

        """
        sp_parent_list = list()
        for sp in self.species:
            if isinstance(sp.Parent, (float, np.float, type(None))):
                sp_parent_list.append(sp.Abbreviation)
            else:
                sp_parent_list.append(sp.Parent)
        return np.array([sp_parent_list[i] for i in self.indices])

    def get_parent_basis(self):
        """
        Returns the basis with all user defined/special elements as the it's parent

        Returns:
            (pyiron_atomistics.structure.atoms.Atoms) instance

        """
        parent_basis = copy(self)
        new_species = np.array(parent_basis.species)
        for i, sp in enumerate(new_species):
            if not isinstance(sp.Parent, (float, np.float, type(None))):
                pse = PeriodicTable()
                new_species[i] = pse.element(sp.Parent)
        parent_basis.set_species(list(new_species))
        return parent_basis

    def get_chemical_elements(self):
        """

        Returns:

        """
        return self.elements

    def get_number_species_atoms(self):
        """
        
        Returns:

        """
        count = OrderedDict()
        # print "sorted: ", sorted(set(self.elements))
        for el in sorted(set(self.get_chemical_symbols())):
            count[el] = 0

        for el in self.get_chemical_symbols():
            count[el] += 1
        return count

    def get_species_symbols(self):
        """

        Returns:

        """
        return np.array(sorted([el.Abbreviation for el in self.species]))

    def get_species_objects(self):
        """

        Returns:

        """
        el_set = self.species
        el_sym_lst = {el.Abbreviation: i for i, el in enumerate(el_set)}
        el_sorted = self.get_species_symbols()
        return [el_set[el_sym_lst[el]] for el in el_sorted]

    def get_number_of_species(self):
        """
        
        Returns:

        """
        return len(self.species)

    def get_number_of_degrees_of_freedom(self):
        """
        
        Returns:

        """
        return len(self) * self.dimension

    def get_masses(self):
        """

        Returns:

        """
        el_lst = [el.AtomicMass for el in self.species]
        return [el_lst[el] for el in self.indices]

    def get_masses_dof(self):
        """
        
        Returns:

        """
        dim = self.dimension
        return np.repeat(self.get_masses(), dim)

    def get_volume(self):
        """
        
        Returns:

        """
        return np.abs(np.linalg.det(self.cell))

    def get_density(self):
        """
        density in g/cm^3
        
        Returns:

        """
        # conv_factor = Ang3_to_cm3/scipi.constants.Avogadro
        # with Ang3_to_cm3 = 1e24
        conv_factor = 1.660539040427164
        return conv_factor * np.sum(self.get_masses()) / self.get_volume()

    def get_scaled_positions(self, wrap=True):
        """
        
        Returns:

        """
        pbc = np.array(self.pbc)
        positions = copy(self.scaled_positions)
        if wrap:
            positions[:, pbc] = np.mod(positions[:, pbc], 1.)
        return positions

    def get_number_of_atoms(self):
        """
        
        Returns:

        """
        # assert(len(self) == np.sum(self.get_number_species_atoms().values()))
        return len(self)

    def set_absolute(self):
        if self._is_scaled:
            self._is_scaled = False
            self.scaled_positions = self._internal_positions

    def set_relative(self):
        if not self._is_scaled:
            self._is_scaled = True
            self.positions = self._internal_positions

    def center_coordinates_in_unit_cell(self, origin=0, eps=1e-4):
        """
        compact atomic coordinates in supercell as given by a1, a2., a3
        
        Args:
            origin:  0 to confine between 0 and 1, -0.5 to confine between -0.5 and 0.5
            eps: 

        Returns:

        """
        self.scaled_positions = np.mod(self.scaled_positions + eps, 1) - eps + origin
        return self

    def repeat(self, rep):
        """Create new repeated atoms object.

        The *rep* argument should be a sequence of three positive
        integers like *(2,3,1)* or a single integer (*r*) equivalent
        to *(r,r,r)*."""

        atoms = self.copy()
        atoms *= rep
        return atoms

    def set_repeat(self, vec):
        self *= vec

    def reset_absolute(self, is_absolute):
        raise NotImplementedError('This function was removed!')

    def analyse_ovito_cna_adaptive(self, mode='total'):
        import warnings
        from pyiron_atomistics.structure.ovito import analyse_ovito_cna_adaptive
        warnings.filterwarnings("ignore")
        return analyse_ovito_cna_adaptive(atoms=self, mode=mode)

    def plot3d(self, spacefill=True, show_cell=True):
        """

        Returns:

        """
        try:  # If the graphical packages are not available, the GUI will not work.
            import nglview
        except ImportError:
            raise ImportError("The package nglview needs to be installed for the plot3d() function!")
        # Always visualize the parent basis
        parent_basis = self.get_parent_basis()
        view = nglview.show_ase(parent_basis)
        if spacefill:
            view.add_spacefill(radius_type='vdw', scale=0.5)
            # view.add_spacefill(radius=1.0)
            view.remove_ball_and_stick()
        else:
            view.add_ball_and_stick()
        if show_cell:
            if parent_basis.cell is not None:
                view.add_unitcell()
        return view

    def pos_xyz(self):
        """
        
        Returns:

        """
        x = self.positions[:, 0]
        y = self.positions[:, 1]
        z = self.positions[:, 2]
        return x, y, z

    def __select_slice(self, i_dim, i_flag, dist):
        """
        
        Args:
            i_dim: 
            i_flag: 
            dist: 

        Returns:

        """
        if i_dim + 1 > self.dimension:
            return True
        if i_flag == 1:
            return self.scaled_positions[:, i_dim] < dist
        elif i_flag == 0:
            return True
        elif i_flag == -1:
            return self.scaled_positions[:, i_dim] > 1. - dist

    def get_boundary_region(self, dist):
        """
        get all atoms in the boundary around the supercell which have a distance
        to the supercell boundary of less than dist
        
        Args:
            dist: 

        Returns:

        """
        rel_coordinates = self.scaled_positions

        dim = self.dimension
        cell = self.cell.T  # to use same definition as ASE
        a1 = cell[0]
        a2, a3 = 0, 0
        min_i, max_i = -1, 2
        iyl, iy, izl, iz = 0, 1, 0, 1
        if dim > 1:
            a2 = cell[1]
            iyl, iy = min_i, max_i
        if dim > 2:
            a3 = cell[2]
            izl, iz = min_i, max_i

        index = np.arange(len(self))
        new_coordinates = np.zeros((1, dim))
        # pbcVec = np.zeros((1, dim))
        ia_list = np.zeros((1, 1), dtype=np.int)
        for i0 in range(min_i, max_i):
            for i1 in range(iyl, iy):
                for i2 in range(izl, iz):
                    # r_vec_abs = i0 * a1 + i1 * a2 + i2 * a3
                    r_vec = np.array([i0, i1, i2][:dim])
                    select = self.__select_slice(0, i0, dist) & self.__select_slice(1, i1, dist) & \
                             self.__select_slice(2, i2, dist)
                    if np.linalg.norm(r_vec) > 0:
                        if len(select) > 0:
                            sel_coordinates = rel_coordinates[select] + r_vec
                            new_coordinates = np.append(new_coordinates, sel_coordinates, axis=0)
                            if len(sel_coordinates) > 0:
                                # rVecs = np.array(len(sel_coordinates) * [r_vec_abs])
                                # pbcVec = np.append(pbcVec, rVecs, axis=0)
                                ia_list = np.append(ia_list, index[select])
                                # print "rVec: ", i0,i1,i2,rVecs[0],index[select],select

        element_list = [self.indices[ia] for ia in ia_list[1:]]
        self._ia_bounds = ia_list[1:]
        # self._pbcVec = pbcVec[1:]
        return Atoms(indices=element_list, scaled_positions=new_coordinates[1:], cell=self.cell,
                     dimension=len(cell), species=self.species)

    def get_neighbors(self,
                      radius=None,
                      num_neighbors=12,
                      t_vec=True,
                      include_boundary=True,
                      exclude_self=True,
                      tolerance=2,
                      id_list=None, cutoff=None):
        """
        
        Args:
            radius: distance up to which nearest neighbors are searched for
                    used only for periodic boundary padding
                   (in absolute units)
            num_neighbors: 
            t_vec (bool): True: compute distance vectors
                        (pbc are automatically taken into account)
            include_boundary (bool): True: search for neighbors assuming periodic boundary conditions
                                     False is needed e.g. in plot routines to avoid showing incorrect bonds
            exclude_self (bool): include central __atom (i.e. distance = 0)
            tolerance (int): tolerance (round decimal points) used for computing neighbor shells
            id_list:
            cutoff (float): Upper bound of the distance to which the search must be done

        Returns:

        """
        # eps = 1e-4
        i_start = 0
        if exclude_self:
            i_start = 1

        def f_ind(x):
            return x < len(self)

        num_neighbors += 1
        neighbor_obj = Neighbors()
        if not include_boundary:  # periodic boundaries are NOT included
            tree = cKDTree(self.positions)
            if cutoff is None:
                neighbors = tree.query(self.positions, k=num_neighbors)
            else:
                neighbors = tree.query(self.positions, k=num_neighbors, distance_upper_bound=cutoff)

            distances = neighbors[0]
            neighbor_obj.indices = []
            neighbor_obj.distances = []
            indices = list(map(lambda x: filter(f_ind, x), neighbors[1]))
            for i, ind in enumerate(indices):
                ind = list(ind)
                neighbor_obj.indices = np.append(neighbor_obj.indices, ind[i_start:])
                neighbor_obj.distances = np.append(neighbor_obj.distances, distances[i][i_start:len(ind)])
            neighbor_obj.indices = neighbor_obj.indices.astype(int)
            return neighbor_obj

        # include periodic boundaries
        # translate radius in boundary layer with relative coordinates
        # TODO: introduce more rigoros definition
        if radius is None:
            radius = 3 * num_neighbors ** (1. / 3.)
        rel_width = [radius / np.sqrt(np.dot(a_i, a_i)) for a_i in self.cell]
        rel_width_scalar = np.max(rel_width)

        # construct cell with additional atoms bounding original cell
        boundary_atoms = self.get_boundary_region(rel_width_scalar)
        extended_cell = self + boundary_atoms

        # build index to map boundary atoms back to original cell
        map_to_cell = np.append(np.arange(len(self)), self._ia_bounds)

        # transfer relative to absolute coordinates
        tree = cKDTree(extended_cell.positions)
        if id_list is None:
            positions = self.positions
        else:
            positions = np.array([self.positions[i] for i in id_list])
        # print ("len positions: ", len(positions))
        if cutoff is None:
            neighbors = tree.query(positions, k=num_neighbors)
        else:
            neighbors = tree.query(positions, k=num_neighbors, distance_upper_bound=cutoff)

        # print ("neighbors: ", neighbors)

        self.neighbor_distance = []  # neighbors[0]
        self.neighbor_distance_vec = []
        self.neighbor_index = []

        self.neighbor_shellOrder = []

        # tolerance = 2 # tolerance for round floating point

        def f_ind_ext(x):
            return x < len(extended_cell)

        neighbor_index = map(lambda x: filter(f_ind_ext, x), neighbors[1])
        num_neighbors = []
        for i, index in enumerate(neighbor_index):
            # print "i, index: ", i, index
            index = list(index)  # Filter conversion for python 3 compatibility
            nbrs_distances = neighbors[0][i][i_start:len(index)]
            # if radius:  # reduce neighborlist based on radius 
            #     new_index_lst, new_dist_lst = [], []
            #     for index_red, dis_red in zip(index, nbrs_distances):
            #         if dis_red < radius: 
            #             new_index_lst.append(index_red)
            #             new_dist_lst.append(dis_red)
            #     index, nbrs_distances= new_index_lst, new_dist_lst
            self.neighbor_distance.append(nbrs_distances)
            self.neighbor_index.append(map_to_cell[index][i_start:])
            u, indices = np.unique(np.around(nbrs_distances, decimals=tolerance), return_inverse=True)
            self.neighbor_shellOrder.append(indices + 1)  # this gives the shellOrder of neighboring atoms back

            if t_vec:
                nbr_dist = []
                if len(index) == 0:
                    print("index: ", index)
                    self.neighbor_distance_vec.append(nbr_dist)
                    continue
                vec0 = self.positions[index[0]]
                for i_nbr, ind in enumerate(index[i_start:]):
                    # ind0 = map_to_cell[ind]
                    vec_r_ij = extended_cell.positions[ind] - vec0

                    dd0 = neighbors[0][i][i_nbr + i_start]
                    dd = np.sqrt(np.dot(vec_r_ij, vec_r_ij))
                    assert (dd - dd0 < 0.001)
                    # if (dd - dd0 > 0.001):
                    #     print "wrong: ", vec_r_ij, dd,dd0,i_nbr,ind,ind0,i
                    #     print self.positions[ind0], extended_cell.positions[ind], vec0
                    nbr_dist.append(vec_r_ij)
                self.neighbor_distance_vec.append(nbr_dist)
            num_neighbors.append(len(index) - i_start)

        min_nbr, max_nbr = min(num_neighbors), max(num_neighbors)
        if max_nbr == num_neighbors:
            # print "neighbor distance: ", self.neighbor_distance
            raise ValueError("Increase max_num_neighbors! " + str(max_nbr) + " " + str(num_neighbors))
        self.min_nbr_number = min_nbr
        self.max_nbr_number = max_nbr
        neighbor_obj.distances = self.neighbor_distance
        neighbor_obj.vecs = self.neighbor_distance_vec
        neighbor_obj.indices = self.neighbor_index
        neighbor_obj.shells = self.neighbor_shellOrder
        return neighbor_obj

    def get_shells(self, id_list=None, max_shell=2, radius=None, max_num_neighbors=100):
        """
        
        Args:
            id_list: 
            max_shell: 
            radius: 
            max_num_neighbors: 

        Returns:

        """
        if id_list is None:
            id_list = [0]
        neighbors = self.get_neighbors(radius=radius,
                                       num_neighbors=max_num_neighbors,
                                       id_list=id_list)

        shells = neighbors.shells[0]
        dist = neighbors.distances[0]

        shell_dict = {}
        for i_shell in set(shells):
            if i_shell > max_shell:
                break
            shell_dict[i_shell] = np.mean(dist[shells == i_shell])
            # print ("shells: ", i_shell, shell_dict[i_shell])
        assert (max(shell_dict.keys()) == max_shell)
        return shell_dict

    def get_shell_radius(self, shell=1, id_list=None):
        """
        
        Args:
            shell: 
            id_list: 

        Returns:

        """
        if id_list is None:
            id_list = [0]
        shells = self.get_shells(id_list=id_list, max_shell=shell + 1)
        return np.mean(list(shells.values())[shell - 1:])

    def occupy_lattice(self, **qwargs):
        """
        Replaces specified indices with a given species
        """
        new_species = list(np.array(self.species).copy())
        new_indices = np.array(self.indices.copy())
        for key, i_list in qwargs.items():
            el = self._pse.element(key)
            if el.Abbreviation not in [spec.Abbreviation for spec in new_species]:
                new_species.append(el)
                new_indices[i_list] = len(new_species) - 1
            else:
                index = np.argwhere(np.array(new_species) == el).flatten()
                new_indices[i_list] = index
        delete_species_indices = list()
        retain_species_indices = list()
        for i, el in enumerate(new_species):
            if len(np.argwhere(new_indices == i).flatten()) == 0:
                delete_species_indices.append(i)
            else:
                retain_species_indices.append(i)
        for i in delete_species_indices:
            new_indices[new_indices >= i] += -1
        new_species = np.array(new_species)[retain_species_indices]
        self.set_species(new_species)
        self.indices = new_indices

    def cluster_analysis(self, id_list, neighbors=None, radius=None, return_cluster_sizes=False):
        """
        
        Args:
            id_list: 
            neighbors: 
            radius: 
            return_cluster_sizes: 

        Returns:

        """
        if neighbors is None:
            if radius is None:
                radius = self.get_shell_radius()
                # print "radius: ", radius
            neighbors = self.get_neighbors(radius, t_vec=False)
        self._neighbor_index = neighbors.indices
        self._cluster = [0] * len(self)
        c_count = 1
        # element_list = self.get_atomic_numbers()
        for ia in id_list:
            # el0 = element_list[ia]
            nbrs = self._neighbor_index[ia]
            # print ("nbrs: ", ia, nbrs)
            if self._cluster[ia] == 0:
                self._cluster[ia] = c_count
                self.__probe_cluster(c_count, nbrs, id_list)
                c_count += 1

        cluster = np.array(self._cluster)
        cluster_dict = {i_c: np.where(cluster == i_c)[0].tolist() for i_c in range(1, c_count)}
        if return_cluster_sizes:
            sizes = [self._cluster.count(i_c + 1) for i_c in range(c_count - 1)]
            return cluster_dict, sizes

        return cluster_dict  # sizes

    def __probe_cluster(self, c_count, neighbors, id_list):
        """
        
        Args:
            c_count: 
            neighbors: 
            id_list: 

        Returns:

        """
        for nbr_id in neighbors:
            if self._cluster[nbr_id] == 0:
                if nbr_id in id_list:  # TODO: check also for ordered structures
                    self._cluster[nbr_id] = c_count
                    nbrs = self._neighbor_index[nbr_id]
                    self.__probe_cluster(c_count, nbrs, id_list)

    # TODO: combine with corresponding routine in plot3d
    def get_bonds(self, radius=None, max_shells=None, prec=0.1, num_neighbors=20):
        """
        
        Args:
            radius: 
            max_shells: 
            prec: minimum distance between any two clusters (if smaller considered to be single cluster)
            num_neighbors: 

        Returns:

        """

        def get_cluster(dist_vec, ind_vec, prec=prec):
            ind_where = np.where(np.diff(dist_vec) > prec)[0] + 1
            ind_vec_cl = [np.sort(group) for group in np.split(ind_vec, ind_where)]
            dist_vec_cl = [np.mean(group) for group in np.split(dist_vec, ind_where)]
            return ind_vec_cl, dist_vec_cl

        neighbors = self.get_neighbors(radius=radius,
                                       num_neighbors=num_neighbors)

        dist = neighbors.distances
        ind = neighbors.indices
        el_list = self.get_chemical_symbols()

        ind_shell = []
        for i_a, (d, i) in enumerate(zip(dist, ind)):
            id_list, dist_lst = get_cluster(d[d < radius], i[d < radius])
            # print ("id: ", d[d<radius], id_list, dist_lst)
            ia_shells_dict = {}
            for i_shell_list in id_list:
                ia_shell_dict = {}
                for i_s in i_shell_list:
                    el = el_list[i_s]
                    if el not in ia_shell_dict:
                        ia_shell_dict[el] = []
                    ia_shell_dict[el].append(i_s)
                for el, ia_lst in ia_shell_dict.items():
                    if el not in ia_shells_dict:
                        ia_shells_dict[el] = []
                    if max_shells is not None:
                        if len(ia_shells_dict[el]) + 1 > max_shells:
                            continue
                    ia_shells_dict[el].append(ia_lst)
            ind_shell.append(ia_shells_dict)
        return ind_shell

    # spglib calls
    def get_symmetry(self, use_magmoms=False, symprec=1e-5, angle_tolerance=-1.0):
        """
        
        Args:
            use_magmoms: 
            symprec: 
            angle_tolerance: 

        Returns:


        """
        lattice = np.array(self.get_cell().T, dtype='double', order='C')
        positions = np.array(self.get_scaled_positions(), dtype='double', order='C')
        numbers = np.array(self.get_atomic_numbers(), dtype='intc')
        if use_magmoms:
            magmoms = self.get_initial_magnetic_moments()
            return spglib.get_symmetry(cell=(lattice, positions, numbers, magmoms),
                                       symprec=symprec,
                                       angle_tolerance=angle_tolerance)
        else:
            return spglib.get_symmetry(cell=(lattice, positions, numbers),
                                       symprec=symprec,
                                       angle_tolerance=angle_tolerance)

    def get_symmetry_dataset(self, symprec=1e-5, angle_tolerance=-1.0):
        """
        
        Args:
            symprec: 
            angle_tolerance: 

        Returns:

        https://atztogo.github.io/spglib/python-spglib.html
        """
        lattice = np.array(self.get_cell().T, dtype='double', order='C')
        positions = np.array(self.get_scaled_positions(), dtype='double', order='C')
        numbers = np.array(self.get_atomic_numbers(), dtype='intc')
        return spglib.get_symmetry_dataset(cell=(lattice, positions, numbers),
                                           symprec=symprec,
                                           angle_tolerance=angle_tolerance)

    def get_spacegroup(self, symprec=1e-5, angle_tolerance=-1.0):
        """
        
        Args:
            symprec: 
            angle_tolerance: 

        Returns:

        https://atztogo.github.io/spglib/python-spglib.html
        """
        lattice = np.array(self.get_cell(), dtype='double', order='C')
        positions = np.array(self.get_scaled_positions(), dtype='double', order='C')
        numbers = np.array(self.get_atomic_numbers(), dtype='intc')
        space_group = spglib.get_spacegroup(cell=(lattice, positions, numbers),
                                            symprec=symprec,
                                            angle_tolerance=angle_tolerance).split()
        if len(space_group) == 1:
            return {"Number": eval(space_group[0])}
        else:
            return {"InternationalTableSymbol": space_group[0],
                    "Number": eval(space_group[1])}

    def refine_cell(self, symprec=1e-5, angle_tolerance=-1.0):
        """
        
        Args:
            symprec: 
            angle_tolerance: 

        Returns:

        https://atztogo.github.io/spglib/python-spglib.html
        """
        lattice = np.array(self.get_cell().T, dtype='double', order='C')
        positions = np.array(self.get_scaled_positions(), dtype='double', order='C')
        numbers = np.array(self.get_atomic_numbers(), dtype='intc')
        cell, coords, el = spglib.refine_cell(cell=(lattice, positions, numbers),
                                              symprec=symprec,
                                              angle_tolerance=angle_tolerance)

        return Atoms(symbols=list(self.get_chemical_symbols()),
                     positions=coords,
                     cell=cell)

    def get_primitive_cell(self, symprec=1e-5, angle_tolerance=-1.0):
        """
        
        Args:
            symprec: 
            angle_tolerance: 

        Returns:

        """
        el_dict = {}
        for el in set(self.get_chemical_elements()):
            el_dict[el.AtomicNumber] = el
        lattice = np.array(self.get_cell().T, dtype='double', order='C')
        positions = np.array(self.get_scaled_positions(), dtype='double', order='C')
        numbers = np.array(self.get_atomic_numbers(), dtype='intc')
        cell, coords, atomic_numbers = spglib.find_primitive(cell=(lattice, positions, numbers),
                                                             symprec=symprec,
                                                             angle_tolerance=angle_tolerance)
        # print atomic_numbers, type(atomic_numbers)
        el_lst = [el_dict[i_a] for i_a in atomic_numbers]

        # convert lattice vectors to standard (experimental feature!) TODO:
        red_structure = Atoms(elements=el_lst,
                              scaled_positions=coords,
                              cell=cell)
        space_group = red_structure.get_spacegroup(symprec)["Number"]
        # print "space group: ", space_group
        if space_group == 225:  # fcc
            print("WARNING: experimental feature (getPrimitiveCell)")

            alat = np.max(cell[0])
            amat_fcc = alat * np.array([[1, 0, 1], [1, 1, 0], [0, 1, 1]])

            red_structure.cell = amat_fcc
        return red_structure

    def get_ir_reciprocal_mesh(self, mesh, is_shift=np.zeros(3, dtype='intc'), is_time_reversal=True, symprec=1e-5):
        """
        
        Args:
            mesh: 
            is_shift: 
            is_time_reversal: 
            symprec: 

        Returns:

        """
        mapping, mesh_points = spglib.get_ir_reciprocal_mesh(mesh=mesh, cell=self, is_shift=is_shift,
                                                             is_time_reversal=is_time_reversal, symprec=symprec)
        return mapping, mesh_points

    def get_equivalent_atoms(self, eps=1e-5):
        """
        
        Args:
            eps: 

        Returns:

        """
        sym = self.get_symmetry()
        coords = np.mod(self.get_scaled_positions() + eps, 1) - eps

        trans_vec = []
        rot_vec = []
        id_vec = []

        ind_ref = 0  # TODO: extend as loop over all inequivalent atoms
        id_mat = np.identity(3, dtype='intc')
        ref_id_list = []
        for trans, rot in zip(sym["translations"], sym["rotations"]):
            if np.linalg.norm(rot - id_mat) < eps:  # TODO: remove this limitation
                id_list = []
                for i_c, coord_new in enumerate(np.mod(coords - trans + eps, 1) - eps):
                    no_match = True
                    hash_id = None
                    for hash_id, c in enumerate(coords):
                        if np.linalg.norm(coord_new - c) < eps:
                            id_list.append(hash_id)
                            no_match = False
                            break
                    if hash_id == ind_ref:
                        # print "ref_id: ", i_c
                        ref_id_list.append(i_c)

                    # if len(id_vec)==1:
                    #     print "c: ", i_c, coord_new, c
                    if no_match:
                        print("WARNING: getEquivalentAtoms (no match)")
                        print("new: ", coord_new)
                        print("old: ", coords)
                        raise ValueError("No equivalent atom found!")

                trans_vec.append(trans)
                rot_vec.append(rot)
                id_vec.append(id_list)

        eq_atoms = [0]
        # print "ref_id: ", ref_id_list
        return eq_atoms, trans_vec, rot_vec, id_vec, ref_id_list

    def get_majority_species(self):
        """
        
        Returns:

        """
        el_dict = self.get_number_species_atoms()
        el_num = list(el_dict.values())
        el_name = list(el_dict.keys())
        max_index = np.argsort(el_num)[-1]
        return max_index, el_name[max_index]

    def extend(self, other):
        """
        Extend atoms object by appending atoms from *other*. Copied from ase
        
        Args:
            other: 

        Returns:

        """
        if isinstance(other, Atom):
            other = self.__class__([other])
        n1 = len(self)
        n2 = len(other)
        for name, a1 in self._tag_list.items():
                a1 = np.array(a1)
                a = np.zeros((n1 + n2,) + a1.shape[1:], a1.dtype)
                a[:n1] = a1
                if name == 'masses':
                    a2 = other.get_masses()
                else:
                    a2 = other.lists.get(name)
                if a2 is not None:
                    a[n1:] = a2
                self._lists[name] = a
        for name, a2 in other.lists.items():
            if name in self._tag_list.keys():
                continue
            a = np.empty((n1 + n2,) + a2.shape[1:], a2.dtype)
            a[:n1] = a2
            if name == 'masses':
                a[:n1] = self.get_masses()[:n1]
            else:
                a[:n1] = 0
            self._length = n1 + n2
        # Take care of the species and index
        return self

    def append(self, atom):
        """
        Append atom to end. Copied from ase
        
        Args:
            atom: 

        Returns:

        """
        self.extend(self.__class__([atom]))

    def close(self):
        # TODO: implement
        pass

    def get_voronoi_volume(self):
        """
        
        Returns:

        """
        # adopted from http://stackoverflow.com/questions/19634993/volume-of-voronoi-cell-python
        from scipy.spatial import Voronoi, Delaunay
        def tetravol(a, b, c, d):
            """
            Calculates the volume of a tetrahedron, given vertices a,b,c and d (triplets)
            
            Args:
                a: 
                b: 
                c: 
                d: 

            Returns:

            """
            tetravol = abs(np.dot((a - d), np.cross((b - d), (c - d)))) / 6
            return tetravol

        def vol(vor, p):
            """
            Calculate volume of 3d Voronoi cell based on point p. Voronoi diagram is passed in v.
            
            Args:
                vor: 
                p: 

            Returns:

            """
            dpoints = []
            vol = 0
            for v in vor.regions[vor.point_region[p]]:
                dpoints.append(list(vor.vertices[v]))
            tri = Delaunay(np.array(dpoints))
            for simplex in tri.simplices:
                vol += tetravol(np.array(dpoints[simplex[0]]), np.array(dpoints[simplex[1]]),
                                np.array(dpoints[simplex[2]]), np.array(dpoints[simplex[3]]))
            return vol

        vor = Voronoi(self.positions)

        ind_lst, vol_lst = [], []
        for i, p in enumerate(vor.points):
            out = False
            for v in vor.regions[vor.point_region[i]]:
                # print ("regions: ", i, p, v)
                # if v in region_lst:
                #     continue
                # region_lst.append(v)
                if v <= -1:  # a point index of -1 is returned if the vertex is outside the Vornoi diagram, in this application these should be ignorable edge-cases
                    out = True
            if not out:
                pvol = vol(vor, i)
                ind_lst.append(i)
                vol_lst.append(pvol)
                # print ("point "+str(i)+" with coordinates "+str(p)+" has volume "+str(pvol))

        print("total volume= ", np.sum(vol_lst))
        return np.array(ind_lst), np.array(vol_lst)

    def __add__(self, other):
        if isinstance(other, Atoms):
            sum_atoms = copy(self)
            sum_atoms._tag_list = sum_atoms._tag_list + other._tag_list
            sum_atoms.indices = np.append(sum_atoms.indices, other.indices)
            sum_atoms.positions = np.append(sum_atoms.positions, other.positions, axis=0)

            new_species_lst = copy(sum_atoms.species)
            ind_conv = {}
            # self_species_lst = [el.Abbreviation for el in self.species]
            for ind_old, el in enumerate(other.species):
                if el.Abbreviation in sum_atoms._store_elements.keys():
                    # print ('add:: ', el.Abbreviation, self._store_elements)
                    ind_new = sum_atoms._species_to_index_dict[sum_atoms._store_elements[el.Abbreviation]]
                    ind_conv[ind_old] = ind_new
                else:
                    new_species_lst.append(el)
                    sum_atoms._store_elements[el.Abbreviation] = el
                    ind_conv[ind_old] = len(new_species_lst) - 1
            # print('species_lst: ', new_species_lst, ind_conv)

            new_indices = copy(other.indices)
            for key, val in ind_conv.items():
                new_indices[new_indices == key] = val + 1000
            new_indices = np.mod(new_indices, 1000)
            sum_atoms.indices[len(self.indices):] = new_indices
            sum_atoms.set_species(new_species_lst)

            if not len(set(sum_atoms.indices)) == len(sum_atoms.species):
                # print('indices: ', new_array.indices, new_array.species)
                raise ValueError("Adding the atom instances went wrong!")
            return sum_atoms

        elif isinstance(other, Atom):
            other = self.__class__([other])
            return self + other

    def __copy__(self):
        """
        Copies the atoms object

        Returns:
            atoms_new: A copy of the object

        """
        atoms_new = Atoms()
        for key, val in self.__dict__.items():
            if key not in ['_pse']:
                # print ('copy: ', key)
                atoms_new.__dict__[key] = copy(val)

        return atoms_new

    def __delitem__(self, key):
        if isinstance(key, (int, np.int32, np.int64)):
            key = [key]
        new_length = len(self) - len(key)
        key = np.array(key).flatten()
        self.positions = np.delete(self.positions, key, axis=0)
        self.indices = np.delete(self.indices, key, axis=0)
        del self._tag_list[key]
        self._tag_list._length = new_length
        deleted_species_indices = list()
        retain_species_indices = list()
        new_indices = self.indices.copy()
        for i, el in enumerate(self.species):
            if len(self.select_index(el)) == 0:
                deleted_species_indices.append(i)
                new_indices[new_indices >= i] += -1
            else:
                retain_species_indices.append(i)
        new_species = np.array(self.species).copy()[retain_species_indices]
        self.set_species(new_species)
        self.indices = new_indices

    def __eq__(self, other):
        assert (isinstance(other, Atoms))
        conditions = []
        for a_1, a_2 in zip(self, other):
            conditions.append(a_1 == a_2)
        conditions.append(np.alltrue(self.pbc == other.pbc))
        return all(conditions)

    def __ne__(self, other):
        return not self == other

    def __getitem__(self, item):
        new_dict = dict()
        if isinstance(item, int):
            for key, value in self._tag_list.items():
                if item < len(value):
                    if value[item] is not None:
                        new_dict[key] = value[item]
            element = self.species[self.indices[item]]
            index = item
            position = self.positions[item]
            return Atom(element=element, position=position, pse=self._pse, index=index, atoms=self, **new_dict)

        new_array = copy(self)
        new_array.positions = self.positions[item]

        new_indices = self.indices[item].copy()
        new_species_indices, new_proper_indices = np.unique(new_indices, return_inverse=True)
        new_species = [self.species[ind] for ind in new_species_indices]
        new_array.set_species(new_species)
        new_array.indices = new_proper_indices
        new_array._tag_list = self._tag_list[item]
        # new_array._tag_list._length = self._tag_list._length
        new_array._tag_list._length = len(new_array)
        if isinstance(new_array, Atom):
            natoms = len(self)
            if item < -natoms or item >= natoms:
                raise IndexError('Index out of range.')
            new_array.index = item
        return new_array

    def __getattr__(self, item):
        if item in self._tag_list.keys():
            return self._tag_list._lists[item]
        return object.__getattribute__(self, item)

    def __len__(self):
        return len(self.indices)


    def __repr__(self):
        return self.__str__()

    def __str__(self):
        if len(self) == 0:
            return "[]"
        out_str = ""
        for el, pos in zip(self.get_chemical_symbols(), self.positions):
            out_str += el + ": " + str(pos) + "\n"
        if len(self.get_tags()) > 0:
            tags = self.get_tags()
            out_str += "tags: \n"  # + ", ".join(tags) + "\n"
            for tag in tags:
                out_str += "    " + str(tag) + ": " + self._tag_list[tag].__str__() + "\n"
        if self._cell is not None:
            out_str += "pbc: " + str(self.pbc) + "\n"
            out_str += "cell: \n"
            out_str += str(self.cell) + "\n"
        return out_str

    def __setitem__(self, key, value):
        if isinstance(key, (int, np.int8, np.int16, np.int32, np.int64)):
            old_el = self.species[self.indices[key]]
            if isinstance(value, (str, np.str, np.str_)):
                el = PeriodicTable().element(value)
            elif isinstance(value, ChemicalElement):
                el = value
            else:
                raise TypeError('value should either be a string or a ChemicalElement.')
            if el != old_el:
                new_species = np.array(self.species).copy()
                if len(self.select_index(old_el)) == 1:
                    if el.Abbreviation not in [spec.Abbreviation for spec in new_species]:
                        new_species[self.indices[key]] = el
                        self.set_species(list(new_species))
                    else:
                        el_list = np.array([sp.Abbreviation for sp in new_species])
                        ind = np.argwhere(el_list == el.Abbreviation).flatten()[-1]
                        remove_index = self.indices[key]
                        new_species = list(new_species)
                        del new_species[remove_index]
                        self.indices[key] = ind
                        self.indices[self.indices > remove_index] -= 1
                        self.set_species(new_species)
                else:
                    if el.Abbreviation not in [spec.Abbreviation for spec in new_species]:
                        new_species = list(new_species)
                        new_species.append(el)
                        self.set_species(new_species)
                        self.indices[key] = len(new_species) - 1
                    else:
                        el_list = np.array([sp.Abbreviation for sp in new_species])
                        ind = np.argwhere(el_list == el.Abbreviation).flatten()[-1]
                        self.indices[key] = ind
        elif isinstance(key, slice) or isinstance(key, (list, tuple, np.ndarray)):
            if not isinstance(key, slice):
                if hasattr(key, '__len__'):
                    if len(key) == 0:
                        return
            else:
                if key.start is not None:
                    if key.stop is not None:
                        key = np.arange(key.start, key.stop, key.step)
                    else:
                        if key.start >= 0:
                            key = np.arange(key.start, len(self), key.step)
                        else:
                            key = np.arange(len(self) + key.start, len(self), key.step)
                else:
                    if key.stop is not None:
                        key = np.arange(0, key.stop, key.step)
                    else:
                        key = np.arange(0, len(self), key.step)
            if isinstance(value, (str, np.str, np.str_, int, np.int, np.int32)):
                el = PeriodicTable().element(value)
            elif isinstance(value, ChemicalElement):
                el = value
            else:
                raise ValueError("The value assigned should be a string, integer or a ChemicalElement instance")
            replace_list = list()
            new_species = list(np.array(self.species).copy())
            for sp in self.species:
                replace_list.append(np.array_equal(np.sort(self.select_index(sp)),
                                                   np.sort(np.intersect1d(self.select_index(sp), key))))
            if el.Abbreviation not in [spec.Abbreviation for spec in new_species]:
                if not any(replace_list):
                    new_species.append(el)
                    self.set_species(new_species)
                    self.indices[key] = len(new_species) - 1
                else:
                    replace_ind = np.where(replace_list)[0][0]
                    new_species[replace_ind] = el
                    if len(np.where(replace_list)[0]) > 1:
                        for ind in replace_list[1:]:
                            del new_species[ind]
                    self.set_species(new_species)
                    self.indices[key] = replace_ind
            else:
                el_list = np.array([sp.Abbreviation for sp in new_species])
                ind = np.argwhere(el_list == el.Abbreviation).flatten()[-1]
                if not any(replace_list):
                    self.set_species(new_species)
                    self.indices[key] = ind
                else:
                    self.indices[key] = ind
                    for i, rep in enumerate(replace_list):
                        if i != ind and rep:
                            del new_species[i]
                            self.indices[self.indices > i] -= 1
                    self.set_species(new_species)
        else:
            raise NotImplementedError()

    __mul__ = repeat

    def __imul__(self, vec):
        """

        Args:
            vec:

        Returns:

        """
        if isinstance(vec, int):
            vec = [vec] * self.dimension

        assert (len(vec) == self.dimension)

        i_vec = np.array([vec[0], 1, 1])
        if self.dimension > 1:
            i_vec[1] = vec[1]
        if self.dimension > 2:
            i_vec[2] = vec[2]

        if not self.dimension == 3:
            raise NotImplementedError()
        mx, my, mz = i_vec
        nx_lst, ny_lst, nz_lst = np.arange(mx), np.arange(my), np.arange(mz)

        positions = self.scaled_positions

        lat = np.array(np.meshgrid(nx_lst, ny_lst, nz_lst)).T.reshape(-1, 3)
        lat_new = np.repeat(lat, len(positions), axis=0)

        new_positions = np.tile(positions, (len(lat), 1)) + lat_new

        self._length = len(new_positions)
        self.scaled_positions = new_positions/np.array(i_vec)
        self.indices = np.tile(self.indices, len(lat))
        self._tag_list._length = len(self)
        # print ('basis_len: ', len(self.positions), len(new_elements))

        # self.cell = (self.cell.T * np.array(vec)).T
        self.set_cell((self.cell.T * np.array(vec)).T, scale_atoms=True)
        scale = i_vec[0] * i_vec[1] * i_vec[2]
        for tag in self._tag_list.keys():
            self._tag_list[tag] *= scale

        return self  # to make it compatible with ASE

    @staticmethod
    def convert_formula(elements):
        """

        Args:
            elements:

        Returns:

        """
        el_list = []
        num_list = ""
        for i, char in enumerate(elements):
            is_last = (i == len(elements) - 1)
            if len(num_list) > 0:
                if (not char.isdigit()) or is_last:
                    el_fac = eval(num_list) * el_list[-1]
                    for el in el_fac[1:]:
                        el_list.append(el)
                    num_list = ""

            if char.isupper():
                el_list.append(char)
            elif char.islower():
                el_list[-1] += char
            elif char.isdigit():
                num_list += char

            if len(num_list) > 0:
                # print "num_list: ", el_list, num_list, el_list[-1], (not char.isdigit()) or is_last
                if (not char.isdigit()) or is_last:
                    el_fac = eval(num_list) * [el_list[-1]]
                    # print "el_fac: ", el_fac
                    for el in el_fac[1:]:
                        el_list.append(el)
                    num_list = ""

        return el_list

    @staticmethod
    def _test_neighbors(indices):
        """

        Args:
            indices:

        Returns:

        """
        for ia, ind in enumerate(indices):
            print("index: ", ia, ind)

        for ia, ind in enumerate(indices):
            for i in ind:
                if ia not in indices[i]:
                    print("ia: ", ia, i, indices[i])
                    raise ValueError('corrupt bond')

    # ASE compatibility
    @staticmethod
    def get_calculator():
        return None

    def get_cell(self, complete=False):
        """Get the three unit cell vectors as a 3x3 ndarray."""
        if complete:
            return complete_cell(self._cell)
        else:
            return self._cell.copy()

    def get_distance(self, a0, a1, mic=False, vector=False):
        """
        Return distance between two atoms.

        Use mic=True to use the Minimum Image Convention.
        vector=True gives the distance vector (from a0 to a1).

        Args:
            a0:
            a1:
            mic:
            vector:

        Returns:

        """
        from ase.utils.geometry import find_mic

        positions = self.positions
        distance = np.array([positions[a1] - positions[a0]])
        print("Warning: get_distance may fail")
        print("atoms.get_distance: ", np.linalg.norm(distance))
        if mic:
            print("Periodic Boundary conditions do not work")
            distance, d_len = find_mic(distance, self.cell, self.pbc)
        else:
            d_len = np.array([np.sqrt((distance ** 2).sum())])
        if vector:
            return distance[0]

        return d_len[0]

    def get_constraint(self):
        if 'selective_dynamics' in self._tag_list._lists.keys():
            from ase.constraints import FixAtoms
            return FixAtoms(indices=np.array([atom_ind for atom_ind in range(len(self))
                                              if any(self.selective_dynamics[atom_ind])]))
        else:
            return None

    def set_constraint(self, constrain):
        if constrain.todict()['name'] != 'FixAtoms':
            raise ValueError('Only FixAtoms is supported as ASE compatible constraint.')
        if 'selective_dynamics' not in self._tag_list._lists.keys():
            self.add_tag(selective_dynamics=None)
        for atom_ind in range(len(self)):
            if atom_ind in constrain.index:
                self.selective_dynamics[atom_ind] = [True, True, True]
            else:
                self.selective_dynamics[atom_ind] = [False, False, False]
                
    def get_initial_magnetic_moments(self):
        """
        Get array of initial magnetic moments.
    
        Returns:
            numpy.array()
        """
        if 'spin' in self._tag_list._lists.keys():
            return np.array(list(self.spin.values()))
        else:
            spin_lst = [element.tags['spin'] if 'spin' in element.tags.keys() else None
                        for element in self.get_chemical_elements()]
            if any(spin_lst):
                if (isinstance(spin_lst, str) or 
                    (isinstance(spin_lst, (list, np.ndarray)) and isinstance(spin_lst[0], str))
                   ) and '[' in list(set(spin_lst))[0]:
                    return np.array(
                        [[float(spin_dir) for spin_dir in spin.replace('[', '').replace(']', '').replace(',', '').split()]
                         if spin else [0.0, 0.0, 0.0] for spin in spin_lst])
                elif isinstance(spin_lst, (list, np.ndarray)):
                    return np.array(spin_lst)
                else:
                    return np.array([float(spin) if spin else 0.0 for spin in spin_lst])
            else:
                return np.array([None] * len(self))

    def set_initial_magnetic_moments(self, magmoms):
        """
        Set array of initial magnetic moments.

        Args:
            magmoms (numpy.array()):
        """
        if magmoms is not None:
            if len(magmoms) != len(self):
                raise ValueError('magmons can be collinear or non-collinear.')
            for ind, element in enumerate(self.get_chemical_elements()):
                if 'spin' in element.tags.keys():
                    print('Overwrite: ' + str(element.Abbreviation) + ' with ' + str(element.Parent) + ' when using per ' +
                          'atom spins.')
                    self[ind] = element.Parent
            if 'spin' not in self._tag_list._lists.keys():
                self.add_tag(spin=None)
            for ind, spin in enumerate(magmoms):
                self.spin[ind] = spin

    def pop(self, i=-1):
        """
        Remove and return atom at index *i* (default last).

        Args:
            i:

        Returns:

        """
        atom = self[i]
        atom.cut_reference_to_atoms()
        del self[i]
        return atom

    def rotate(self, vector, angle=None, center=(0, 0, 0), rotate_cell=False, index_list=None):
        """
        Rotate atoms based on a vector and an angle, or two vectors.

        Args:

            rotate_cell:
            center:
            vector (list/numpy.ndarray/string):
                Vector to rotate the atoms around. Vectors can be given as
                strings: 'x', '-x', 'y', ... .

            angle (float/list) in radians = None:
                Angle that the atoms is rotated around the vecor 'v'. If an angle
                is not specified, the length of 'v' is used as the angle
                (default). The angle can also be a vector and then 'v' is rotated
                into 'a'.

            center = [0, 0, 0]:
                The center is kept fixed under the rotation. Use 'COM' to fix
                the center of mass, 'COP' to fix the center of positions or
                'COU' to fix the center of cell.

            rotate_cell = False:
                If true the cell is also rotated.

            index_list (list/numpy.ndarray):
                Indices of atoms to be rotated

        Examples:

        Rotate 90 degrees around the z-axis, so that the x-axis is
        rotated into the y-axis:

        >>> atoms = Atoms('H', [[-0.1, 1.01, -0.5]], cell=[[1, 0, 0], [0, 1, 0], [0, 0, 4]], pbc=[1, 1, 0])
        >>> a = (22./ 7.) / 2. # pi/2
        >>> atoms.rotate('z', a)
        >>> atoms.rotate((0, 0, 1), a)
        >>> atoms.rotate('-z', -a)
        >>> atoms.rotate((0, 0, a))
        >>> atoms.rotate('x', 'y')
        """

        norm = np.linalg.norm
        vector = string2vector(vector)
        if angle is None:
            angle = norm(vector)
        if isinstance(angle, (float, int)):
            vector /= norm(vector)
            c = cos(angle)
            s = sin(angle)
        else:
            v2 = string2vector(angle)
            vector /= norm(vector)
            v2 /= norm(v2)
            c = np.dot(vector, v2)
            vector = np.cross(vector, v2)
            s = norm(vector)
            # In case *v* and *a* are parallel, np.cross(v, v2) vanish
            # and can't be used as a rotation axis. However, in this
            # case any rotation axis perpendicular to v2 will do.
            eps = 1e-7
            if s < eps:
                vector = np.cross((0, 0, 1), v2)
                if norm(vector) < eps:
                    vector = np.cross((1, 0, 0), v2)
                assert norm(vector) >= eps
            elif s > 0:
                vector /= s

        if isinstance(center, str):
            if center.lower() == 'com':
                center = self.get_center_of_mass()
            elif center.lower() == 'cop':
                center = np.mean(self.get_positions(), axis=0)
            elif center.lower() == 'cou':
                center = self.cell.sum(axis=0) / 2
            else:
                raise ValueError('Cannot interpret center')
        else:
            center = np.array(center)

        if index_list is not None:
            assert (len(index_list) > 0)
            rotate_list = index_list
        else:
            rotate_list = [range(len(self))]

        p = self.positions[rotate_list] - center
        self.positions[rotate_list] = (c * p -
                                       np.cross(p, s * vector) +
                                       np.outer(np.dot(p, vector), (1.0 - c) * vector) +
                                       center)
        if rotate_cell:
            rotcell = self.cell
            rotcell[:] = (c * rotcell -
                          np.cross(rotcell, s * vector) +
                          np.outer(np.dot(rotcell, vector), (1.0 - c) * vector))
            self.cell = rotcell

    def rotate_euler(self, center=(0, 0, 0), phi=0.0, theta=0.0, psi=0.0):
        """Rotate atoms via Euler angles.

        See e.g http://mathworld.wolfram.com/EulerAngles.html for explanation.

        Parameters:

        center :
            The point to rotate about. a sequence of length 3 with the
            coordinates, or 'COM' to select the center of mass, 'COP' to
            select center of positions or 'COU' to select center of cell.
        phi :
            The 1st rotation angle around the z axis.
        theta :
            Rotation around the x axis.
        psi :
            2nd rotation around the z axis.

        """
        if isinstance(center, str):
            if center.lower() == 'com':
                center = self.get_center_of_mass()
            elif center.lower() == 'cop':
                center = self.get_positions().mean(axis=0)
            elif center.lower() == 'cou':
                center = self.cell.sum(axis=0) / 2
            else:
                raise ValueError('Cannot interpret center')
        else:
            center = np.array(center)

        # First move the molecule to the origin In contrast to MATLAB,
        # numpy broadcasts the smaller array to the larger row-wise,
        # so there is no need to play with the Kronecker product.
        rcoords = self.positions - center
        # First Euler rotation about z in matrix form
        d = np.array(((cos(phi), sin(phi), 0.),
                      (-sin(phi), cos(phi), 0.),
                      (0., 0., 1.)))
        # Second Euler rotation about x:
        c = np.array(((1., 0., 0.),
                      (0., cos(theta), sin(theta)),
                      (0., -sin(theta), cos(theta))))
        # Third Euler rotation, 2nd rotation about z:
        b = np.array(((cos(psi), sin(psi), 0.),
                      (-sin(psi), cos(psi), 0.),
                      (0., 0., 1.)))
        # Total Euler rotation
        a = np.dot(b, np.dot(c, d))
        # Do the rotation
        rcoords = np.dot(a, np.transpose(rcoords))
        # Move back to the rotation point
        self.positions = np.transpose(rcoords) + center

    def set_scaled_positions(self, scaled):
        """
        Set positions relative to unit cell.

        Args:
            scaled:

        Returns:

        """
        self.scaled_positions = scaled

    def set_cell(self, cell, scale_atoms=False):
        """Set unit cell vectors.

        Parameters:

        cell: 3x3 matrix or length 3 or 6 vector
            Unit cell.  A 3x3 matrix (the three unit cell vectors) or
            just three numbers for an orthorhombic cell. Another option is
            6 numbers, which describes unit cell with lengths of unit cell
            vectors and with angles between them (in degrees), in following
            order: [len(a), len(b), len(c), angle(b,c), angle(a,c),
            angle(a,b)].  First vector will lie in x-direction, second in
            xy-plane, and the third one in z-positive subspace.
        scale_atoms: bool
            Fix atomic positions or move atoms with the unit cell?
            Default behavior is to *not* move the atoms (scale_atoms=False).

        Examples:

        Two equivalent ways to define an orthorhombic cell:

        >>> atoms = Atoms('He')
        >>> a, b, c = 7, 7.5, 8
        >>> atoms.set_cell([a, b, c])
        >>> atoms.set_cell([(a, 0, 0), (0, b, 0), (0, 0, c)])

        FCC unit cell:

        >>> atoms.set_cell([(0, b, b), (b, 0, b), (b, b, 0)])

        Hexagonal unit cell:

        >>> atoms.set_cell([a, a, c, 90, 90, 120])

        Rhombohedral unit cell:

        >>> alpha = 77
        >>> atoms.set_cell([a, a, a, alpha, alpha, alpha])
        """

        cell = np.array(cell, float)

        if cell.shape == (3,):
            cell = np.diag(cell)
        elif cell.shape == (6,):
            cell = cellpar_to_cell(cell)
        elif cell.shape != (3, 3):
            raise ValueError('Cell must be length 3 sequence, length 6 '
                             'sequence or 3x3 matrix!')

        if scale_atoms:
            M = np.linalg.solve(self.get_cell(complete=True),
                                complete_cell(cell))
            self.positions[:] = np.dot(self.positions, M)
        self._cell = cell

    def translate(self, displacement):
        """
        Translate atomic positions.

        The displacement argument can be a float, an xyz vector, or an
        nx3 array (where n is the number of atoms).

        Args:
            displacement:

        Returns:

        """
        self.positions += np.array(displacement)

    def wrap(self, center=(0.5, 0.5, 0.5), pbc=None, eps=1e-7):
        """Wrap positions to unit cell.

        Parameters:

        center: three float
            The positons in fractional coordinates that the new positions
            will be nearest possible to.
        pbc: one or 3 bool
            For each axis in the unit cell decides whether the positions
            will be moved along this axis.  By default, the boundary
            conditions of the Atoms object will be used.
        eps: float
            Small number to prevent slightly negative coordinates from beeing
            wrapped.

        See also the :func:`ase.utils.geometry.wrap_positions` function.
        Example:

        >>> a = Atoms('H',
        ...           [[-0.1, 1.01, -0.5]],
        ...           cell=[[1, 0, 0], [0, 1, 0], [0, 0, 4]],
        ...           pbc=[1, 1, 0])
        >>> a.wrap()
        >>> a.positions
        array([[ 0.9 ,  0.01, -0.5 ]])
        """

        from ase.utils.geometry import wrap_positions
        if pbc is None:
            pbc = self.pbc
        self.positions = wrap_positions(self.positions, self.cell,
                                        pbc, center, eps)

    def write(self, filename, format=None, **kwargs):
        """
        Write atoms object to a file.

        see ase.io.write for formats.
        kwargs are passed to ase.io.write.
        
        Args:
            filename: 
            format: 
            **kwargs: 

        Returns:

        """
        from ase.io import write
        write(filename, self, format, **kwargs)


class _CrystalStructure(Atoms):
    """
    only for historical reasons
        
    Args:
        element: 
        BravaisLattice: 
        BravaisBasis: 
        LatticeConstants: 
        Dimension: 
        relCoords: 
        PSE: 
        **kwargs: 
    """

    def __init__(self,
                 element="Fe",
                 bravais_lattice='cubic',
                 bravais_basis='primitive',
                 lattice_constants=None,  # depending on symmetry length and angles
                 dimension=3,
                 rel_coords=True,
                 pse=None,
                 **kwargs):

        # print "basis0"
        # allow also for scalar input for LatticeConstants (for a cubic system)
        if lattice_constants is None:
            lattice_constants = [1.]
        try:
            test = lattice_constants[0]
        except (TypeError, IndexError):
            lattice_constants = [lattice_constants]
        self.bravais_lattice = bravais_lattice
        self.bravais_basis = bravais_basis
        self.lattice_constants = lattice_constants
        self.dimension = dimension
        self.relCoords = rel_coords
        self.element = element

        self.__updateCrystal__(pse)

        self.crystalParamsDict = {'BravaisLattice': self.bravais_lattice, 'BravaisBasis': self.bravais_basis,
                                  'LatticeConstants': self.lattice_constants}

        self.crystal_lattice_dict = {3: {
            "cubic": ["fcc", "bcc", "primitive"],
            "hexagonal": ["primitive", "hcp"],
            "monoclinic": ["primitive", "base-centered"],
            "triclinic": ["primitive"],
            "orthorombic": ["primitive", "body-centered", "base-centered", "face-centered"],
            "tetragonal": ["primitive", "body-centered"],
            "rhombohedral": ["primitive"]}, 2: {
            "oblique": ["primitive"],
            "rectangular": ["primitive", "centered"],
            "hexagonal": ["primitive"],
            "square": ["primitive"]}, 1: {"line": ["primitive"]}}

        # init structure for lattice parameters alat, blat, clat, alpha, beta, gamma
        self.crystalLatticeParams = {3: {"cubic": [1.],
                                         "hexagonal": [1., 2.],
                                         "monoclinic": [1., 1., 1., 90.],
                                         "triclinic": [1., 2., 3., 90., 90., 90.],
                                         "orthorombic": [1., 1., 1.],
                                         "tetragonal": [1., 2.],
                                         "rhombohedral": [1., 90., 90., 90.]}, 2: {"oblique": [1., 1., 90.],
                                                                                   "rectangular": [1., 1.],
                                                                                   "hexagonal": [1.],
                                                                                   "square": [1.]}, 1: {"line": [1.]}}

        # print "basis"
        super(_CrystalStructure, self).__init__(elements=self.ElementList,
                                                scaled_positions=self.coordinates,
                                                cell=self.amat,  # tag = "Crystal",
                                                pbc=[True, True, True][0:self.dimension])

    # ## private member functions
    def __updateCrystal__(self, pse=None):
        """
        
        Args:
            pse: 

        Returns:

        """
        self.__updateAmat__()
        self.__updateCoordinates__()
        self.__updateElementList__(pse)

    def __updateAmat__(self):  # TODO: avoid multi-call of this function
        """
        
        Returns:

        """
        # print "lat constants (__updateAmat__):", self.LatticeConstants
        a_lat = self.lattice_constants[0]

        if self.dimension == 3:
            alpha = None
            beta = None
            gamma = None
            b_lat, c_lat = None, None
            if self.bravais_lattice == 'cubic':
                b_lat = c_lat = a_lat
                alpha = beta = gamma = 90 / 180. * np.pi  # 90 degrees
            elif self.bravais_lattice == 'tetragonal':
                b_lat = a_lat
                c_lat = self.lattice_constants[1]
                alpha = beta = gamma = 0.5 * np.pi  # 90 degrees
            elif self.bravais_lattice == 'triclinic':
                b_lat = self.lattice_constants[1]
                c_lat = self.lattice_constants[2]
                alpha = self.lattice_constants[3] / 180. * np.pi
                beta = self.lattice_constants[4] / 180. * np.pi
                gamma = self.lattice_constants[5] / 180. * np.pi
            elif self.bravais_lattice == 'hexagonal':
                b_lat = a_lat
                c_lat = self.lattice_constants[1]
                alpha = 60. / 180. * np.pi  # 60 degrees
                beta = gamma = 0.5 * np.pi  # 90 degrees
            elif self.bravais_lattice == 'orthorombic':
                b_lat = self.lattice_constants[1]
                c_lat = self.lattice_constants[2]
                alpha = beta = gamma = 0.5 * np.pi  # 90 degrees
            elif self.bravais_lattice == 'rhombohedral':
                b_lat = a_lat
                c_lat = a_lat
                alpha = self.lattice_constants[1] / 180. * np.pi
                beta = self.lattice_constants[2] / 180. * np.pi
                gamma = self.lattice_constants[3] / 180. * np.pi
            elif self.bravais_lattice == 'monoclinic':
                b_lat = self.lattice_constants[1]
                c_lat = self.lattice_constants[2]
                alpha = 0.5 * np.pi
                beta = self.lattice_constants[3] / 180. * np.pi
                gamma = 0.5 * np.pi

            b1 = np.cos(alpha)
            b2 = np.sin(alpha)
            c1 = np.cos(beta)
            c2 = (np.cos(gamma) - np.cos(beta) * np.cos(alpha)) / np.sin(alpha)
            self.amat = np.array([[a_lat, 0., 0.],
                                  [b_lat * b1, b_lat * b2, 0.],
                                  [c_lat * c1, c_lat * c2, c_lat * np.sqrt(1 - c2 * c2 - c1 * c1)]])
        elif self.dimension == 2:  # TODO not finished yet
            self.amat = a_lat * np.array([[1., 0.], [0., 1.]])
            if self.bravais_lattice == 'rectangular':
                b_lat = self.lattice_constants[1]
                self.amat = np.array([[a_lat, 0.], [0., b_lat]])
        elif self.dimension == 1:
            self.amat = a_lat * np.array([[1.]])
        else:
            raise ValueError("Bravais lattice not defined!")

    def __updateElementList__(self, pse=None):
        """
        
        Args:
            pse: 

        Returns:

        """
        self.ElementList = len(self.coordinates) * [self.element]

    def __updateCoordinates__(self):
        """
        
        Returns:

        """
        # if relative coordinates
        basis = None
        if self.dimension == 3:
            if self.bravais_basis == "fcc" or self.bravais_basis == "face-centered":
                basis = np.array([[0., 0., 0.], [0.5, 0.5, 0.], [0.5, 0., 0.5], [0., 0.5, 0.5]])
            elif self.bravais_basis == "body-centered" or self.bravais_basis == "bcc":
                basis = np.array([[0., 0., 0.], [0.5, 0.5, 0.5]])
            elif self.bravais_basis == "base-centered":
                basis = np.array([[0., 0., 0.], [0.5, 0.5, 0.]])
            elif self.bravais_basis == "hcp":
                # basis = r([[0.0,-1./np.sqrt(3.),np.sqrt(8./3.)]])
                # a = self.LatticeConstants[0]
                # c = self.LatticeConstants[1]
                basis = np.array([[0., 0., 0.], [1. / 3., 1. / 3., 1. / 2.]])
                # basis = np.dot(basis,np.linalg.inv(self.amat))
            elif self.bravais_basis == "primitive":
                basis = np.array([[0., 0., 0.]])
            else:
                print("basis name: ", self.bravais_basis, " not known")
                exit()
        elif self.dimension == 2:
            if self.bravais_basis == "primitive":
                basis = np.array([[0., 0.]])
            elif self.bravais_basis == "centered":
                basis = np.array([[0., 0.], [0.5, 0.5]])
            else:
                print("basis name unknown")
                exit()
        elif self.dimension == 1:
            if self.bravais_basis == "primitive":
                basis = np.array([[0.]])
            else:
                print("basis name unknown")
                exit()
        self.coordinates = basis

    # ########################### get commmands ########################
    def get_lattice_types(self):
        """
        
        Returns:

        """
        self.crystal_lattice_dict[self.dimension].keys().sort()
        return self.crystal_lattice_dict[self.dimension].keys()

    def get_dimension_of_lattice_parameters(self):
        """
        
        Returns:

        """
        # print "getDimensionOfLatticeParameters"
        counter = 0
        for k in self.get_needed_lattice_parameters():
            if k:
                counter += 1
        return counter

    def get_needed_lattice_parameters(self):
        """
        
        Returns:

        """
        # print "call: getNeededLatticeParams"
        needed_params = [True, False, False, False, False, False]
        if self.dimension == 3:
            if self.bravais_lattice == 'cubic':
                needed_params = [True, False, False, False, False,
                                False]  # stands for alat, blat, clat, alpha, beta, gamma
            elif self.bravais_lattice == 'triclinic':
                needed_params = [True, True, True, True, True, True]
            elif self.bravais_lattice == 'monoclinic':
                needed_params = [True, True, True, True, False, False]
            elif self.bravais_lattice == 'orthorombic':
                needed_params = [True, True, True, False, False, False]
            elif self.bravais_lattice == 'tetragonal':
                needed_params = [True, False, True, False, False, False]
            elif self.bravais_lattice == 'rhombohedral':
                needed_params = [True, False, False, True, True, True]
            elif self.bravais_lattice == 'hexagonal':
                needed_params = [True, False, True, False, False, False]
        elif self.dimension == 2:
            if self.bravais_lattice == 'oblique':
                needed_params = [True, True, False, True, False, False]
            elif self.bravais_lattice == 'rectangular':
                needed_params = [True, True, False, False, False, False]
            elif self.bravais_lattice == 'hexagonal':
                needed_params = [True, False, False, False, False, False]
            elif self.bravais_lattice == 'square':
                needed_params = [True, False, False, False, False, False]
            else:  # TODO: need to be improved
                needed_params = [True, False, False, False, False, False]
        elif self.dimension == 1:
            if self.bravais_lattice == 'line':
                needed_params = [True, False, False, False, False, False]
            else:  # TODO: improval needed
                needed_params = [True, False, False, False, False, False]
        else:
            raise ValueError("inconsistency in lattice structures")

        return needed_params

    def get_basis_types(self):
        """
        
        Returns:

        """
        self.crystal_lattice_dict[self.dimension].get(self.bravais_lattice).sort()
        return self.crystal_lattice_dict[self.dimension].get(self.bravais_lattice)

    def get_initial_lattice_constants(self):
        """
        
        Returns:

        """
        self.crystalLatticeParams[self.dimension].get(self.bravais_lattice).sort()
        return self.crystalLatticeParams[self.dimension].get(self.bravais_lattice).sort()

    # def getDimension(self):
    #     return self.dimension

    # def getCoordinates(self):
    #     return self.coordinates

    # def getCell(self):
    #     return self.amat

    def get_atom_structure(self, rel=True):
        """
        
        Args:
            rel: 

        Returns:

        """
        #        print self.relCoords, self.amat
        return Atoms(
            elementList=self.ElementList,
            coordinates=self.coordinates,
            amat=self.amat,
            tag="Crystal",
            rel=rel,  # self.relCoords, #rel, # true or false # coordinates are given in relative lattice units
            pbc=[True, True, True][0:self.dimension],
            Crystal=self.crystalParamsDict
        )

    # #################### set commands #########################
    def set_lattice_constants(self, lattice_constants=None):
        """
        
        Args:
            lattice_constants: 

        Returns:

        """
        if lattice_constants is None:
            lattice_constants = [1.]
        for k in lattice_constants:
            if k <= 0:
                raise ValueError("negative lattice parameter(s)")
        self.lattice_constants = lattice_constants
        self.__updateCrystal__()

    def set_element(self, element="Fe"):
        """
        
        Args:
            element: 

        Returns:

        """
        self.element = element
        self.__updateCrystal__()

    def set_dimension(self, dim=3):
        """
        
        Args:
            dim: 

        Returns:

        """
        self.dimension = dim
        length = self.get_dimension_of_lattice_parameters()
        if dim == 3:  # # initial 3d structure
            self.lattice_constants = length * [1.]
            self.bravais_lattice = "cubic"
            self.bravais_basis = "primitive"
        elif dim == 2:  # # initial 2d structure
            self.lattice_constants = length * [1.]
            self.bravais_lattice = "square"
            self.bravais_basis = "primitive"
        elif dim == 1:  # # initial 1d structure
            self.lattice_constants = length * [1.]
            self.bravais_lattice = "line"
            self.bravais_basis = "primitive"
        self.__updateCrystal__()

    def set_lattice_type(self, name_lattice='cubic'):
        """
        
        Args:
            name_lattice: 

        Returns:

        """
        # catch input error
        # print "lattice type =", name_lattice
        if name_lattice not in self.get_lattice_types():
            print(name_lattice, "is not item of", self.dimension, "d lattice types:")
            print(self.get_lattice_types())
            raise ValueError("is not item of ")
        else:
            self.bravais_lattice = name_lattice
            self.set_lattice_constants(self.get_dimension_of_lattice_parameters() * [1.])
            self.set_basis_type(
                name_basis=self.crystal_lattice_dict[self.dimension].get(name_lattice)[0])  # initial basis type

        self.__updateCrystal__()

    def set_basis_type(self, name_basis='primitive'):
        """
        
        Args:
            name_basis: 

        Returns:

        """
        if name_basis not in self.get_basis_types():  # crystalLatticeDict[self.Dimension].get(self.BravaisLattice):
            print(name_basis, "is not item of", self.bravais_lattice, "lattice:")
            print(self.get_basis_types())
            raise ValueError("is not item of")
        else:
            self.bravais_basis = name_basis
        self.__updateCrystal__()

    def atoms(self):
        """
        
        Returns:

        """
        return Atoms(elements=self.ElementList,
                     scaled_positions=self.coordinates,
                     cell=self.amat,
                     pbc=[True, True, True][0:self.dimension])


class Neighbors:
    """
    Class for storage of the neighbor information for a given atom based on the KDtree algorithm
    """

    def __init__(self):
        self._distances = None
        self._vecs = None
        self._indices = None
        self._shells = None

    @property
    def distances(self):
        return self._distances

    @distances.setter
    def distances(self, new_distances):
        if isinstance(new_distances, list) or isinstance(new_distances, np.ndarray):
            self._distances = np.array(new_distances)
        else:
            raise TypeError('Only lists and np.arrays are supported.')

    @property
    def vecs(self):
        return self._vecs

    @vecs.setter
    def vecs(self, new_vecs):
        if isinstance(new_vecs, list) or isinstance(new_vecs, np.ndarray):
            self._vecs = np.array(new_vecs)
        else:
            raise TypeError('Only lists and np.arrays are supported.')

    @property
    def indices(self):
        return self._indices

    @indices.setter
    def indices(self, new_indices):
        if isinstance(new_indices, list) or isinstance(new_indices, np.ndarray):
            self._indices = np.array(new_indices)
        else:
            raise TypeError('Only lists and np.arrays are supported.')

    @property
    def shells(self):
        return self._shells

    @shells.setter
    def shells(self, new_shells):
        if isinstance(new_shells, list) or isinstance(new_shells, np.array):
            self._shells = np.array(new_shells)
        else:
            raise TypeError('Only lists and np.arrays are supported.')


class CrystalStructure(object):
    def __new__(cls, *args, **kwargs):
        basis = _CrystalStructure(*args, **kwargs).atoms()
        return basis


def ase_to_pyiron(ase_obj):
    """
    
    Args:
        ase_obj: 

    Returns:

    """
    try:
        import ase
    except ImportError:
        raise ValueError('ASE package not yet installed')
    element_list = ase_obj.get_chemical_symbols()
    cell = ase_obj.cell
    positions = ase_obj.get_positions()
    pbc = ase_obj.get_pbc()
    return Atoms(elements=element_list, positions=positions, pbc=pbc, cell=cell)

def pyiron_to_ase(pyiron_obj):
    try:
        from pyiron_atomistics.structure.pyironase import ASEAtoms
    except ImportError:
        raise ValueError('ASE package not yet installed')
    element_list = pyiron_obj.get_chemical_symbols()
    cell = pyiron_obj.cell
    positions = pyiron_obj.positions
    pbc = pyiron_obj.get_pbc()
    atoms = ASEAtoms(symbols=element_list, positions=positions, pbc=pbc, cell=cell)
    return atoms

def pymatgen_to_pyiron(pymatgen_obj):
    try:
        from pymatgen.io.ase import AseAtomsAdaptor
    except ImportError:
        raise ValueError('PyMatGen package not yet installed')
    return ase_to_pyiron(AseAtomsAdaptor().get_atoms(structure=pymatgen_obj))

def pyiron_to_pymatgen(pyiron_obj):
    try:
        from pymatgen.io.ase import AseAtomsAdaptor
    except ImportError:
        raise ValueError('PyMatGen package not yet installed')
    return AseAtomsAdaptor().get_structure(atoms=pyiron_to_ase(pyiron_obj), cls=None)

def ovito_to_pyiron(ovito_obj):
    """
    
    Args:
        ovito_obj: 

    Returns:

    """
    try:
        from ovito.data import ase_to_pyiron
        return ase_to_pyiron(ovito_obj.to_ase_atoms())
    except ImportError:
        raise ValueError('ovito package not yet installed')


def pyiron_to_ovito(atoms):
    """
    
    Args:
        atoms: 

    Returns:

    """
    try:
        from ovito.data import DataCollection
        return DataCollection.create_from_ase_atoms(atoms)
    except ImportError:
        raise ValueError('ovito package not yet installed')


## copy from ase.atoms to allow class overloading

def string2symbols(s):
    """
    Convert string to list of chemical symbols.
    
    Args:
        s: 

    Returns:

    """
    i = None
    n = len(s)
    if n == 0:
        return []
    c = s[0]
    if c.isdigit():
        i = 1
        while i < n and s[i].isdigit():
            i += 1
        return int(s[:i]) * string2symbols(s[i:])
    if c == '(':
        p = 0
        for i, c in enumerate(s):
            if c == '(':
                p += 1
            elif c == ')':
                p -= 1
                if p == 0:
                    break
        j = i + 1
        while j < n and s[j].isdigit():
            j += 1
        if j > i + 1:
            m = int(s[i + 1:j])
        else:
            m = 1
        return m * string2symbols(s[1:i]) + string2symbols(s[j:])

    if c.isupper():
        i = 1
        if 1 < n and s[1].islower():
            i += 1
        j = i
        while j < n and s[j].isdigit():
            j += 1
        if j > i:
            m = int(s[i:j])
        else:
            m = 1
        return m * [s[:i]] + string2symbols(s[j:])
    else:
        raise ValueError


def symbols2numbers(symbols):
    """

    Args:
        symbols (list, str):

    Returns:

    """
    pse = PeriodicTable()
    df = pse.dataframe.T
    if isinstance(symbols, str):
        symbols = string2symbols(symbols)
    numbers = list()
    for sym in symbols:
        if isinstance(sym, string_types):
            numbers.append(df[sym]["AtomicNumber"])
        else:
            numbers.append(sym)
    return numbers


def string2vector(v):
    """
    
    Args:
        v: 

    Returns:

    """
    if isinstance(v, str):
        if v[0] == '-':
            return -string2vector(v[1:])
        w = np.zeros(3)
        w['xyz'.index(v)] = 1.0
        return w
    return np.array(v, float)


def default(data, dflt):
    """
    Helper function for setting default values.
    
    Args:
        data: 
        dflt: 

    Returns:

    """
    if data is None:
        return None
    elif isinstance(data, (list, tuple)):
        newdata = []
        allnone = True
        for x in data:
            if x is None:
                newdata.append(dflt)
            else:
                newdata.append(x)
                allnone = False
        if allnone:
            return None
        return newdata
    else:
        return data
