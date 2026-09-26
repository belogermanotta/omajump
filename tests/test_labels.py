from __future__ import annotations

import unittest

from omajump.labels import DEFAULT_ALPHABET, generate_labels, is_prefix_free


class LabelTests(unittest.TestCase):
    def test_empty(self) -> None:
        self.assertEqual(generate_labels(0), [])

    def test_single_key_labels_are_used_first(self) -> None:
        self.assertEqual(generate_labels(len(DEFAULT_ALPHABET)), list(DEFAULT_ALPHABET))

    def test_expansion_remains_prefix_free(self) -> None:
        for count in (10, 17, 18, 81, 250):
            labels = generate_labels(count)
            self.assertEqual(len(labels), count)
            self.assertEqual(len(set(labels)), count)
            self.assertTrue(is_prefix_free(labels))
            self.assertLessEqual(max(map(len, labels)), 3)

    def test_earliest_targets_keep_short_labels(self) -> None:
        labels = generate_labels(10)
        self.assertEqual(labels[:8], list(DEFAULT_ALPHABET[:-1]))
        self.assertTrue(all(len(label) == 2 for label in labels[8:]))

    def test_invalid_alphabet_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            generate_labels(3, "a")
        with self.assertRaises(ValueError):
            generate_labels(3, "aab")


if __name__ == "__main__":
    unittest.main()
