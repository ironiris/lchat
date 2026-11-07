import unittest
from io import StringIO

from rich.console import Console
from rich.syntax import Syntax

from lchat_math import cell_width, display_math, render_math_in_markdown, unicode_math


class MathTests(unittest.TestCase):
    def test_quadratic_formula(self):
        source = r'x = \frac{-b \pm \sqrt{b^{2} - 4ac}}{2a}'
        result = display_math(source)
        self.assertIn('√(b² - 4ac)', result)
        self.assertIn('±', result)
        self.assertIn('2a', result)
        self.assertIn('─', result)
        self.assertNotIn('\\frac', result)
        self.assertNotIn('\\sqrt', result)

    def test_cjk_fraction_alignment_and_no_indent_guides(self):
        source = r'\text{산란 강도} \propto \frac{1}{\lambda^4}'
        lines = display_math(source).splitlines()
        bar_column = cell_width(lines[1].split('─')[0])
        self.assertEqual(cell_width(lines[0].split('1')[0]), bar_column)
        self.assertEqual(cell_width(lines[2].split('λ')[0]), bar_column)
        markdown = render_math_in_markdown(r'\[' + source + r'\]')
        code = markdown.split('```text\n', 1)[1].split('\n```', 1)[0]
        output = StringIO()
        Console(file=output, width=80, force_terminal=False).print(Syntax(code, 'text', indent_guides=True))
        self.assertNotIn('│', output.getvalue())
        self.assertIn('λ⁴', output.getvalue())

    def test_boxed_quadratic_formula(self):
        formula = r'\boxed{x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a}}'
        for source in (formula, r'\[' + formula + r'\]', '$$' + formula + '$$'):
            result = render_math_in_markdown(source)
            self.assertIn('┌', result)
            self.assertIn('┘', result)
            self.assertIn('√(b² - 4ac)', result)
            self.assertIn('±', result)
            self.assertIn('2a', result)
            self.assertNotIn(r'\boxed', result)
        self.assertEqual(render_math_in_markdown('`' + formula + '`'), '`' + formula + '`')
        self.assertEqual(render_math_in_markdown('```latex\n' + formula + '\n```'), '```latex\n' + formula + '\n```')
        self.assertIn('√(b² - 4ac)', render_math_in_markdown(r'\(' + formula + r'\)'))
        self.assertEqual(render_math_in_markdown(r'\boxed{x = \frac{1}{2}'), r'\boxed{x = \frac{1}{2}')

    def test_common_delimiters_and_inline(self):
        for source in (r'\[x=\frac{1}{\sqrt{x}}\]', '$$x=\\frac{1}{\\sqrt{x}}$$', '\\[\nx=\\frac{1}{\\sqrt{x}}\n\\]'):
            result = render_math_in_markdown(source)
            self.assertIn('```text', result)
            self.assertIn('√x', result)
            self.assertIn('─', result)
        self.assertEqual(render_math_in_markdown(r'판별식 $b^2-4ac$ 와 \(x_1\)'), '판별식 b²-4ac 와 x₁')

    def test_raw_and_delimited_physics_expressions(self):
        source = r'1+\cos²θ, a \ll λ, d\Omega, (3σ_(R))/(16π) (1+\cos²θ)'
        expected = '1+cos²θ, a ≪ λ, dΩ, (3σ₍R₎)/(16π) (1+cos²θ)'
        self.assertEqual(render_math_in_markdown(source), expected)
        delimited = r'\(1+\cos^2\theta, a \ll \lambda, d\Omega, \frac{3\sigma_{R}}{16\pi}\)'
        self.assertEqual(render_math_in_markdown(delimited), '1+cos²θ, a ≪ λ, dΩ, (3σ₍R₎)/(16π)')
        self.assertEqual(render_math_in_markdown(r'`\cos²θ` 와 \cos²θ'), '`\\cos²θ` 와 cos²θ')
        self.assertEqual(render_math_in_markdown(r'\cosmic \unknown \\cos'), r'\cosmic \unknown \\cos')

    def test_code_and_unknown_commands_are_preserved(self):
        code = '```python\nprice = "$x$"\n```\n`$x$`'
        self.assertEqual(render_math_in_markdown(code), code)
        self.assertEqual(unicode_math(r'\unknown{x}'), r'\unknown{x}')
        self.assertIn(r'\unknown', unicode_math(r'\unknown{x}'))
        self.assertIn(r'\sqrt', unicode_math(r'\sqrt{incomplete'))
        self.assertEqual(render_math_in_markdown('가격 $10 및 $20'), '가격 $10 및 $20')
        self.assertEqual(display_math(r'\sqrt{\frac{1}{2}}'), '√((1)/(2))')

if __name__ == '__main__':
    unittest.main()
