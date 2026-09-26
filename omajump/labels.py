"""Prefix-free home-row label generation."""

from __future__ import annotations


DEFAULT_ALPHABET = "asdfghjkl"


def generate_labels(count: int, alphabet: str = DEFAULT_ALPHABET) -> list[str]:
    """Return deterministic, shortest-first labels with no prefix collisions.

    Leaves at the end of the alphabet are expanded first. This preserves the
    shortest labels for the earliest (top-left) targets while ensuring that an
    exact match can always be activated immediately.
    """

    if count < 0:
        raise ValueError("count must be non-negative")
    if count == 0:
        return []
    if len(alphabet) < 2 or len(set(alphabet)) != len(alphabet):
        raise ValueError("alphabet must contain at least two unique characters")

    leaves = list(alphabet)
    while len(leaves) < count:
        shortest = min(len(label) for label in leaves)
        # Expand the latest shortest leaf. Earlier spatial targets therefore
        # keep their shorter labels, while the tree remains balanced.
        expand_at = max(index for index, label in enumerate(leaves) if len(label) == shortest)
        prefix = leaves.pop(expand_at)
        leaves[expand_at:expand_at] = [prefix + char for char in alphabet]

    # A subset of a prefix-free leaf set remains prefix-free. Prefer the
    # shortest leaves when an expansion produced more capacity than required.
    ranked = sorted(enumerate(leaves), key=lambda item: (len(item[1]), item[0]))
    return [label for _, label in ranked[:count]]


def is_prefix_free(labels: list[str]) -> bool:
    ordered = sorted(labels, key=len)
    return all(
        not candidate.startswith(prefix)
        for index, prefix in enumerate(ordered)
        for candidate in ordered[index + 1 :]
    )
