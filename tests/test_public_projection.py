from pathlib import Path
import importlib.util
import unittest

spec = importlib.util.spec_from_file_location('public_life_projection', Path(__file__).parents[1] / 'life/public_projection.py')
projection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(projection)


class PublicProjectionTests(unittest.TestCase):
    def test_private_free_text_never_enters_public_projection(self):
        state = {'current_activity': 'PRIVATE_BOOK_AND_PERSON', 'current_location': 'PRIVATE_PLACE',
                 'sleep_phase': 'awake', 'energy': 65, 'mood_valence': .3, 'relationships': ['PRIVATE']}
        result = projection.public_life_state(state, {'kind': 'leisure', 'summary': 'PRIVATE_SUMMARY'})
        self.assertTrue(result['can_browse'])
        self.assertEqual(result['activity_kind'], 'leisure')
        self.assertNotIn('PRIVATE', str(result))
        self.assertEqual(result['current_activity'], '自由活动')

    def test_only_structured_idle_and_awake_permit_browsing(self):
        for kind, phase in [('study', 'awake'), ('sleep', 'awake'), ('leisure', 'deep_sleep'), ('rest', 'woken'), ('unknown', 'awake')]:
            self.assertFalse(projection.public_life_state({'sleep_phase': phase}, {'kind': kind})['can_browse'])
        self.assertFalse(projection.public_life_state({}, {})['can_browse'])

    def test_nonfinite_or_unbounded_numbers_are_omitted(self):
        result = projection.public_life_state({'sleep_phase': 'awake', 'energy': float('nan'), 'mood_valence': 12}, {'kind': 'rest'})
        self.assertNotIn('energy', result)
        self.assertNotIn('mood_valence', result)
