from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bus_app.map_view import latlon_to_world_pixels, world_pixels_to_latlon


class MapMathTests(unittest.TestCase):
    def test_world_pixel_round_trip_is_stable(self) -> None:
        latitude = 51.973356
        longitude = 7.572865
        zoom = 16

        world_x, world_y = latlon_to_world_pixels(latitude, longitude, zoom)
        result_latitude, result_longitude = world_pixels_to_latlon(world_x, world_y, zoom)

        self.assertAlmostEqual(result_latitude, latitude, places=5)
        self.assertAlmostEqual(result_longitude, longitude, places=5)


if __name__ == "__main__":
    unittest.main()
