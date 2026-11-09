from io import BytesIO
import json
from unittest.mock import patch

from django.test import TestCase, override_settings


class WeatherDashboardTests(TestCase):
    @override_settings(
        OPENWEATHER_API_KEY="test-key",
        OPENWEATHER_CITY="Manila,PH",
    )
    @patch("detection.views.urlopen")
    def test_dashboard_displays_current_weather(self, mock_urlopen):
        response_body = {
            "weather": [{"description": "light rain", "icon": "10d"}],
            "main": {"temp": 29.6, "humidity": 78},
            "wind": {"speed": 3.2},
            "name": "Manila",
            "sys": {"country": "PH"},
        }
        mock_urlopen.return_value.__enter__.return_value.read.return_value = (
            json.dumps(response_body).encode()
        )

        response = self.client.get("/damage/")

        self.assertContains(response, "30")
        self.assertContains(response, "Light rain")
        self.assertContains(response, "Manila, PH")
        self.assertContains(response, "Humidity: 78%")
        self.assertContains(response, "Wind: 12 km/h")
        self.assertNotContains(response, "test-key")
        mock_urlopen.assert_called_once()

    @override_settings(OPENWEATHER_API_KEY="")
    @patch("detection.views.urlopen")
    def test_dashboard_explains_when_api_key_is_not_configured(
        self,
        mock_urlopen,
    ):
        response = self.client.get("/damage/")

        self.assertContains(response, "Set OPENWEATHER_API_KEY")
        mock_urlopen.assert_not_called()


class WeatherForecastPageTests(TestCase):
    @override_settings(OPENWEATHER_API_KEY="test-key")
    @patch("detection.views.urlopen")
    def test_search_displays_current_conditions_and_daily_forecast(
        self,
        mock_urlopen,
    ):
        current = {
            "weather": [{"description": "clear sky", "icon": "01d"}],
            "main": {"temp": 27.4, "humidity": 65},
            "wind": {"speed": 2},
            "name": "Tokyo",
            "sys": {"country": "JP"},
        }
        forecast = {
            "city": {"timezone": 0},
            "list": [
                {
                    "dt": 1794214800,
                    "main": {"temp_min": 18, "temp_max": 23},
                    "weather": [{"description": "few clouds", "icon": "02d"}],
                },
                {
                    "dt": 1794225600,
                    "main": {"temp_min": 20, "temp_max": 25},
                    "weather": [{"description": "clear sky", "icon": "01d"}],
                },
                {
                    "dt": 1794301200,
                    "main": {"temp_min": 17, "temp_max": 22},
                    "weather": [{"description": "light rain", "icon": "10d"}],
                },
            ],
        }
        mock_urlopen.side_effect = [
            BytesIO(json.dumps(current).encode()),
            BytesIO(json.dumps(forecast).encode()),
        ]

        response = self.client.get(
            "/damage/weather/",
            {"location": "Tokyo, JP"},
        )

        self.assertContains(response, "Tokyo, JP")
        self.assertContains(response, "Clear sky")
        self.assertContains(response, "5-day forecast")
        self.assertContains(response, "Clear sky")
        self.assertContains(response, "Light rain")
        self.assertEqual(mock_urlopen.call_count, 2)
        self.assertIn("q=Tokyo%2C+JP", mock_urlopen.call_args_list[0].args[0])
        self.assertIn("/forecast?", mock_urlopen.call_args_list[1].args[0])

    @override_settings(OPENWEATHER_API_KEY="")
    @patch("detection.views.urlopen")
    def test_forecast_page_shows_setup_message_without_api_key(
        self,
        mock_urlopen,
    ):
        response = self.client.get("/damage/weather/")

        self.assertContains(response, "Set OPENWEATHER_API_KEY")
        mock_urlopen.assert_not_called()
