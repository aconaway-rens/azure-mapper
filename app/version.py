"""Single source of truth for the application version.

Kept here rather than read from git so a tarball download — which carries no
git metadata — reports the same version as a clone. Bump this in the same
commit that pins the README's install instructions to a new tag.
"""

__version__ = "1.5.0"
