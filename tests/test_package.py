from importlib.metadata import version


def test_package_version():
    v = version("face-id-verification")
    assert v == "0.1.0"
