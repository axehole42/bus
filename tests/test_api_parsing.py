from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bus_app.api import BusradarClient
from bus_app.utils import format_delay, format_eta, haversine_meters, parse_lines


class ParsingTests(unittest.TestCase):
    def test_parse_lines_removes_duplicates_and_defaults(self) -> None:
        self.assertEqual(parse_lines("5, 11,22,11"), ("5", "11", "22"))
        self.assertEqual(parse_lines(" , "), ("5", "11", "22"))

    def test_delay_and_eta_formatting(self) -> None:
        self.assertEqual(format_delay(0), "on time")
        self.assertEqual(format_delay(61), "+2 min")
        self.assertEqual(format_eta(1_050, now=1_000), "1 min")
        self.assertEqual(format_eta(900, now=1_000), "due")

    def test_distance_helper_returns_reasonable_value(self) -> None:
        distance = haversine_meters(51.962, 7.625, 51.972, 7.635)
        self.assertGreater(distance, 1_200)
        self.assertLess(distance, 1_400)

    def test_parse_stop_departure_and_vehicle_payloads(self) -> None:
        client = BusradarClient()

        stop = client._parse_stop(  # type: ignore[attr-defined]
            {
                "properties": {
                    "lbez": "Dieckmannstra\u00dfe B",
                    "nr": "4670501",
                    "kbez": "DKM_B",
                    "richtung": "ausw\u00e4rts",
                    "global_id": "de:05515:46705:2:B",
                },
                "geometry": {"coordinates": [7.571909, 51.957391]},
            }
        )
        departure = client._parse_departure(  # type: ignore[attr-defined]
            {
                "sequenz": 43,
                "fahrtbezeichner": "110326_100022_7_1_543",
                "delay": 84,
                "haltid": "4670501",
                "abfahrtszeit": 1773233040,
                "einsteigeverbot": "false",
                "fahrzeugid": "6353",
                "prognosemoeglich": "true",
                "linientext": "1",
                "besetztgrad": "Schwach besetzt",
                "richtungstext": "Roxel Hallenbad",
                "tatsaechliche_abfahrtszeit": 1773233124,
            }
        )
        vehicle = client._parse_vehicle(  # type: ignore[attr-defined]
            {
                "properties": {
                    "linienid": "4",
                    "richtungsid": "1",
                    "akthst": "43591",
                    "delay": 76,
                    "nachhst": "43600",
                    "richtungstext": "Gelmer",
                    "starthst": "44040",
                    "betriebstag": "2026-03-11",
                    "sequenz": 9,
                    "linientext": "4",
                    "fahrzeugid": "5618",
                    "fahrtstatus": "Ist",
                    "fahrtbezeichner": "110326_100124_10_4_564",
                    "abfahrtstart": "1773231120",
                    "visfahrplanlagezst": 1773232001,
                    "ankunftziel": "1773235080",
                    "zielhst": "46301",
                },
                "geometry": {"coordinates": [7.6233233, 51.9438439]},
            }
        )

        self.assertTrue(stop.label.startswith("Dieckmannstra"))
        self.assertEqual(departure.vehicle_id, "6353")
        self.assertTrue(departure.boarding_allowed)
        self.assertEqual(departure.actual_departure, 1773233124)
        self.assertEqual(vehicle.vehicle_id, "5618")
        self.assertEqual(vehicle.line, "4")


if __name__ == "__main__":
    unittest.main()
