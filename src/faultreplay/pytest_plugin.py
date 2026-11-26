def pytest_addoption(parser):
    parser.addoption("--faultreplay-simulator", help="Override the generated test's module:adapter")
