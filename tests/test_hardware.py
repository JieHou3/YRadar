import unittest

from vin_hw import (
    assess_hw4, assess_matrix_headlights, model_year_from_vin,
)


class HardwareAssessmentTests(unittest.TestCase):
    def test_2025_vin_is_likely_not_confirmed(self):
        vin = '7SAYGDEE9SF123456'
        self.assertEqual(model_year_from_vin(vin), 2025)
        self.assertEqual(assess_hw4(vin, 'my')['status'], 'likely')
        self.assertEqual(assess_matrix_headlights(vin, 'my')['status'], 'likely')

    def test_short_year_like_string_is_unknown(self):
        self.assertIsNone(model_year_from_vin('123456789S'))
        self.assertEqual(assess_hw4('123456789S', 'my')['status'], 'unknown')

    def test_model3_does_not_use_model_y_cutoff(self):
        vin = '5YJ3E1EA1PF900001'
        result = assess_hw4(vin, 'm3')
        self.assertEqual(result['status'], 'unknown')
        self.assertIn('Model 3', result['reason'])

    def test_listing_evidence_precedes_vin(self):
        vin = '7SAYGDEE9PF100001'
        result = assess_hw4(vin, 'my', {'Hardware': 'AI4 / HW4'})
        self.assertEqual(result['status'], 'confirmed')
        self.assertEqual(result['source'], 'tesla_listing')

    def test_conflicting_hardware_evidence_is_unknown(self):
        vin = '7SAYGDEE9SF123456'
        result = assess_hw4(vin, 'my', {'Hardware': 'HW3 and HW4'})
        self.assertEqual(result['status'], 'unknown')

    def test_matrix_false_does_not_match_field_name(self):
        vin = '7SAYGDEE9SF123456'
        result = assess_matrix_headlights(vin, 'my', {'MatrixHeadlights': False})
        self.assertEqual(result['status'], 'unlikely')
        self.assertEqual(result['source'], 'tesla_listing')

    def test_early_austin_is_mixed_not_missing(self):
        result = assess_matrix_headlights('7SAYGDEE9PA159999', 'my')
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['source'], 'vin_mixed_production')


if __name__ == '__main__':
    unittest.main()
