from dataclasses import dataclass
from typing import List, Optional


@dataclass
class EvalFixture:
    id: str
    category: str
    title: str
    code: str
    question: str
    expected_type: str  # "syntax", "style", "runtime", "logic", "security", "clean"
    expected_line: Optional[int]
    expected_indicator: str


EVAL_FIXTURES: List[EvalFixture] = [
    EvalFixture(
        id="fix_01_syntax_colon",
        category="syntax",
        title="Missing Colon in Function Definition",
        code="def calculate_grade(score)\n    if score >= 90:\n        return 'A'\n    return 'B'",
        question="Why does Python say syntax error at line 1?",
        expected_type="syntax",
        expected_line=1,
        expected_indicator="expected ':'",
    ),
    EvalFixture(
        id="fix_02_syntax_unclosed_paren",
        category="syntax",
        title="Unclosed Parenthesis",
        code="numbers = [1, 2, 3, 4]\ntotal = sum((x * 2 for x in numbers)\nprint(total)",
        question="My script won't run and points to line 3.",
        expected_type="syntax",
        expected_line=2,
        expected_indicator="was never closed",
    ),
    EvalFixture(
        id="fix_03_undefined_variable",
        category="style",
        title="Typo in Variable Name (Undefined Name)",
        code="def greet_user(username):\n    greeting = 'Hello, '\n    return greeting + user_name\n",
        question="I tried running greet_user but I get a NameError.",
        expected_type="style",
        expected_line=3,
        expected_indicator="undefined name 'user_name'",
    ),
    EvalFixture(
        id="fix_04_unused_imports_and_vars",
        category="style",
        title="Unused Imports and Dead Variables",
        code="import math\nimport os\n\ndef square(n):\n    temp = n * 2\n    return n * n\n",
        question="Can you review my code for unnecessary code?",
        expected_type="style",
        expected_line=1,
        expected_indicator="imported but unused",
    ),
    EvalFixture(
        id="fix_05_runtime_zero_division",
        category="runtime",
        title="ZeroDivisionError on Empty List",
        code="def compute_average(values):\n    return sum(values) / len(values)\n\nresult = compute_average([])\n",
        question="Why does my average function crash when values is empty?",
        expected_type="runtime",
        expected_line=2,
        expected_indicator="ZeroDivisionError",
    ),
    EvalFixture(
        id="fix_06_runtime_index_error",
        category="runtime",
        title="IndexError Off-By-One",
        code="def get_last_element(arr):\n    return arr[len(arr)]\n\nval = get_last_element([10, 20, 30])\n",
        question="I want to get the last element of the list but it crashes.",
        expected_type="runtime",
        expected_line=2,
        expected_indicator="IndexError",
    ),
    EvalFixture(
        id="fix_07_runtime_infinite_recursion",
        category="runtime",
        title="Missing Base Case in Recursion",
        code="def recursive_sum(n):\n    return n + recursive_sum(n - 1)\n\nans = recursive_sum(5)\n",
        question="My recursive function never stops and crashes with maximum recursion depth.",
        expected_type="runtime",
        expected_line=2,
        expected_indicator="RecursionError",
    ),
    EvalFixture(
        id="fix_08_runtime_type_error",
        category="runtime",
        title="String and Integer Concatenation",
        code="def format_user_id(prefix, user_id):\n    return prefix + user_id\n\nout = format_user_id('id_', 12345)\n",
        question="TypeError: can only concatenate str to str.",
        expected_type="runtime",
        expected_line=2,
        expected_indicator="TypeError",
    ),
    EvalFixture(
        id="fix_09_logic_off_by_one",
        category="logic",
        title="Loop Range Off-By-One",
        code="def sum_to_n(n):\n    total = 0\n    for i in range(n):\n        total += i\n    return total\n",
        question="sum_to_n(3) should return 6 (1+2+3), but it returns 3.",
        expected_type="logic",
        expected_line=4,
        expected_indicator="range",
    ),
    EvalFixture(
        id="fix_10_logic_wrong_return_type",
        category="logic",
        title="Returning String Instead of Boolean",
        code="def is_even(num):\n    if num % 2 == 0:\n        return 'True'\n    else:\n        return 'False'\n",
        question="In my if-statement, if is_even(3) is always truthy!",
        expected_type="logic",
        expected_line=3,
        expected_indicator="bool",
    ),
    EvalFixture(
        id="fix_11_security_sandbox_write",
        category="security",
        title="Unauthorized Filesystem Write in Sandbox",
        code="with open('unauthorized_output.txt', 'w') as f:\n    f.write('hacked')\n",
        question="Can student code write to the host file system?",
        expected_type="security",
        expected_line=1,
        expected_indicator="PermissionError",
    ),
    EvalFixture(
        id="fix_12_clean_correct_code",
        category="clean",
        title="Clean Palindrome Checker",
        code="def is_palindrome(text: str) -> bool:\n    cleaned = ''.join(ch.lower() for ch in text if ch.isalnum())\n    return cleaned == cleaned[::-1]\n",
        question="Is my palindrome function clean and bug-free?",
        expected_type="clean",
        expected_line=None,
        expected_indicator="clean",
    ),
]
