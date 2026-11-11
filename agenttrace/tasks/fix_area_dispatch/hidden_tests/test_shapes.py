import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import unittest
from shapes import circle
from dispatch import get_total_area

class TestAreas(unittest.TestCase):
    def test_circle_area(self):
        self.assertAlmostEqual(circle.area(1), 3.141592653589793, places=9)
        self.assertAlmostEqual(circle.area(2), 12.566370614359172, places=9)

    def test_dispatch_total(self):
        # 1 个半径 1 的圆 + 1 个边长 2 的正方形 = pi + 4
        self.assertAlmostEqual(
            get_total_area([("circle", 1), ("square", 2)]),
            3.141592653589793 + 4.0, places=9,
        )
