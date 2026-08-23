# Root conftest. Intentionally empty of fixtures.
#
# Its presence is what puts the repository root on sys.path under pytest's
# default "prepend" import mode, so `import sampark` will resolve from tests/
# in later phases without `pip install -e .` — which would require [project]
# and [build-system] tables in pyproject.toml that this project deliberately
# does not have.
