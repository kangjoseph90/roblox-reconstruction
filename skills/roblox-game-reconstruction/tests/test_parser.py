"""Keep conditional expressions opaque without losing table boundaries."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from luau_static import Parser, plain


class ConditionalTableTests(unittest.TestCase):
    def test_conditional_values_at_table_start_and_after_operators(self):
        for value in ('if flag then "a" else "b"', '1 + if flag then 2 else 3',
                      'not if flag then true else false', '1 * if flag then 2 elseif other then 3 else 4'):
            with self.subTest(value=value):
                tables, errors = Parser('return { '+value+', "last" }').constructors()
                self.assertEqual(errors, [])
                self.assertEqual(tables[0]['entry_count'], 2)
                first = tables[0]['entries'][0]['value']
                self.assertEqual(first['kind'], 'expression')
                self.assertEqual(plain(first)['$expression'].strip(), value)
                self.assertFalse(plain(first)['$evaluated'])

    def test_statement_if_inside_an_anonymous_function_keeps_its_end(self):
        tables, errors = Parser('return {function() if flag then return 1 else return 2 end end, "last"}').constructors()
        self.assertEqual(errors, [])
        self.assertEqual(tables[0]['entry_count'], 2)
