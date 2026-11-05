# See LICENSE for licensing information.
#
# Copyright (c) 2016-2026 Regents of the University of California and The Board
# of Regents for the Oklahoma Agricultural and Mechanical College
# (acting for and on behalf of Oklahoma State University)
# All rights reserved.
#
"""Pure behavioral contract and cycle model for a replicated 2R1W register file.

The public model represents ordered ``[R, R, W]`` ports on one common clock.
Each successful :meth:`RegisterFileModel.tick` returns the post-edge values for
reads sampled at that edge; same-address reads of the accepted write see its new
data through explicit bypass. There is no reset and storage starts uninitialized.
This module models behavior only; it provides no native layout implementation.
"""
from dataclasses import dataclass
from typing import Optional


REGISTER_FILE_PORT_ROLES = ("R", "R", "W")
NATIVE_LAYOUT_MAX_PORTS = 2


class UnsupportedRegisterFileLayout(ValueError):
    """Stable rejection for native register-file layouts beyond the compiler limit."""

    code = "unsupported-register-file-layout"

    def __init__(self, total_ports):
        self.total_ports = total_ports
        super().__init__(
            "{}: native register-file layout supports at most {} total physical ports; got {}".format(
                self.code, NATIVE_LAYOUT_MAX_PORTS, total_ports
            )
        )


def reject_unsupported_layout(total_ports):
    """Reject native layouts over two total physical ports, without fake support.

    The native compiler layout boundary counts all physical read and write
    ports. Counts of one or two may proceed to an independently implemented
    native backend; this Phase 1 module does not generate such a layout.
    """
    if type(total_ports) is not int:
        raise TypeError("total_ports must be an integer")
    if total_ports < 1:
        raise ValueError("total_ports must be positive")
    if total_ports > NATIVE_LAYOUT_MAX_PORTS:
        raise UnsupportedRegisterFileLayout(total_ports)


def validate_port_roles(roles):
    """Validate the exact ordered two-reader/one-writer interface."""
    if type(roles) not in (tuple, list):
        raise TypeError("port roles must be a tuple or list")
    if any(type(role) is not str for role in roles):
        raise TypeError("each port role must be a string")
    if tuple(roles) != REGISTER_FILE_PORT_ROLES:
        raise ValueError("register-file port roles must be exactly [R, R, W]")
    return REGISTER_FILE_PORT_ROLES


class RegisterFileError(ValueError):
    """Base class for invalid register-file model operations."""


class UninitializedReadError(RegisterFileError):
    """A read requested an address not initialized by an earlier or current write."""

    def __init__(self, port, address):
        self.port = port
        self.address = address
        super().__init__("read{} requested uninitialized address {}".format(port, address))


class CopyDivergenceError(RegisterFileError):
    """The two replicated storage copies no longer contain the same state."""

    def __init__(self, address, copy0, copy1):
        self.address = address
        self.copy0 = copy0
        self.copy1 = copy1
        super().__init__(
            "replicated storage diverged at address {}: copy0={!r}, copy1={!r}".format(
                address, copy0, copy1
            )
        )


@dataclass(frozen=True)
class CycleResult:
    """Read outputs after one successful common-clock edge.

    ``readN_valid`` is true only when that port sampled a read in this tick.
    Disabled outputs hold their previous register value; before their first
    valid read they are ``None``. A successful enabled read is always known.
    """

    cycle: int
    read0: Optional[int]
    read1: Optional[int]
    read0_valid: bool
    read1_valid: bool


class RegisterFileModel:
    """Deterministic two-copy cycle model for a `[R, R, W]` register file.

    Args:
        num_words: Positive number of independently addressable words.
        word_size: Positive number of data bits in each word.
        port_roles: Optional explicit ordered port-role tuple/list.

    ``tick`` samples independent read enables/addresses and one write enable
    at one common edge. A write commits to both storage copies on that edge.
    Reads of that same address return the new write data, on either or both
    read ports. Reads of equal addresses and distinct addresses are both valid.
    Uninitialized reads are rejected rather than zero-filled. If any enabled
    read is invalid, the entire attempted tick is rejected without committing
    its write or changing registered outputs.
    """

    def __init__(self, num_words, word_size, port_roles=REGISTER_FILE_PORT_ROLES):
        if type(num_words) is not int:
            raise TypeError("num_words must be an integer")
        if num_words < 1:
            raise ValueError("num_words must be positive")
        if type(word_size) is not int:
            raise TypeError("word_size must be an integer")
        if word_size < 1:
            raise ValueError("word_size must be positive")
        validate_port_roles(port_roles)
        self.num_words = num_words
        self.word_size = word_size
        self._data_mask = (1 << word_size) - 1
        # Missing keys are uninitialized; in particular, no implicit zero state.
        self._copies = ({}, {})
        self._outputs = [None, None]
        self._cycle = 0

    @property
    def cycle(self):
        """Number of successfully committed common-clock edges."""
        return self._cycle

    def assert_copy_consistency(self):
        """Raise :class:`CopyDivergenceError` if replicated storage differs."""
        copy0, copy1 = self._copies
        for address in sorted(set(copy0) | set(copy1)):
            value0 = copy0.get(address)
            value1 = copy1.get(address)
            if address not in copy0 or address not in copy1 or value0 != value1:
                raise CopyDivergenceError(address, value0, value1)

    def tick(self, *, read0_enable, read0_address, read1_enable, read1_address,
             write_enable, write_address=None, write_data=None):
        """Apply one edge and return its registered read outputs.

        All enables must be actual ``bool`` values, addresses actual ``int``
        values within the declared depth, and write data an actual ``int``
        within the declared word width. Both read addresses are explicit even
        when their read enable is low. Write address/data must be ``None`` when
        writing is disabled and both must be supplied when writing is enabled.
        """
        for name, value in (("read0_enable", read0_enable),
                            ("read1_enable", read1_enable),
                            ("write_enable", write_enable)):
            if type(value) is not bool:
                raise TypeError(name + " must be a bool")
        self._check_address(read0_address, "read0_address")
        self._check_address(read1_address, "read1_address")
        if write_enable:
            self._check_address(write_address, "write_address")
            if type(write_data) is not int:
                raise TypeError("write_data must be an integer")
            if not 0 <= write_data <= self._data_mask:
                raise ValueError("write_data does not fit the declared word_size")
        elif write_address is not None or write_data is not None:
            raise ValueError("write_address and write_data must be None when writing is disabled")

        self.assert_copy_consistency()
        sampled = [None, None]
        read_enables = (read0_enable, read1_enable)
        read_addresses = (read0_address, read1_address)
        copy0, copy1 = self._copies
        for port in (0, 1):
            if not read_enables[port]:
                continue
            address = read_addresses[port]
            if write_enable and address == write_address:
                sampled[port] = write_data
            else:
                storage = copy0 if port == 0 else copy1
                if address not in storage:
                    raise UninitializedReadError(port, address)
                sampled[port] = storage[address]

        # All input and read validation has completed; commit atomically now.
        if write_enable:
            copy0[write_address] = write_data
            copy1[write_address] = write_data
        for port in (0, 1):
            if read_enables[port]:
                self._outputs[port] = sampled[port]
        self.assert_copy_consistency()
        self._cycle += 1
        return CycleResult(
            cycle=self._cycle,
            read0=self._outputs[0],
            read1=self._outputs[1],
            read0_valid=read0_enable,
            read1_valid=read1_enable,
        )

    def _check_address(self, address, label):
        if type(address) is not int:
            raise TypeError(label + " must be an integer")
        if not 0 <= address < self.num_words:
            raise ValueError(label + " is outside the declared depth")
