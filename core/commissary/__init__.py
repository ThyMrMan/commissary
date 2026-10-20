"""Commissary — this fork's own code, kept apart from upstream SoulSync.

Commissary tracks upstream (Nezreka/SoulSync) and merges it regularly. Every
line edited inside an upstream file is a future merge conflict, so fork
behaviour lives here and upstream files only carry small, clearly-marked
hooks that call into this package (search for ``Commissary:``).
"""
