import unittest


class BuildAgentBundleTests(unittest.TestCase):
    def test_bundler_moved(self) -> None:
        self.skipTest(
            "Bundler tests live in the tmates-agents repository alongside the bundling toolkit."
        )


if __name__ == "__main__":
    unittest.main()
