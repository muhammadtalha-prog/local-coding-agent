import pytest
from sandbox.add_two_numbers import add_two_numbers

def test_add_positive_integers():
    assert add_two_numbers(2, 3) == 5, "Test case 1 failed"

def test_add_negative_integers():
    assert add_two_numbers(-1, -1) == -2, "Test case 2 failed"

def test_add_zero_and_positive_integer():
    assert add_two_numbers(0, 5) == 5, "Test case 3 failed"

def test_add_zero_and_negative_integer():
    assert add_two_numbers(0, -3) == -3, "Test case 4 failed"

def test_add_zero_and_zero():
    assert add_two_numbers(0, 0) == 0, "Test case 5 failed"

def test_add_positive_floats():
    assert add_two_numbers(1.5, 2.5) == 4.0, "Test case 6 failed"

def test_add_negative_floats():
    assert add_two_numbers(-1.5, -2.5) == -4.0, "Test case 7 failed"

def test_add_mixed_integers_and_floats():
    assert add_two_numbers(3, 2.5) == 5.5, "Test case 8 failed"

def test_add_large_positive_integers():
    assert add_two_numbers(10**9, 10**9) == 2*10**9, "Test case 9 failed"

def test_add_large_negative_integers():
    assert add_two_numbers(-10**9, -10**9) == -2*10**9, "Test case 10 failed"

def test_add_boundary_values():
    assert add_two_numbers(1.7976931348623157e+308, 1.7976931348623157e+308) == 3.5953862697246314e+308, "Test case 11 failed"

def test_add_boundary_values_negative():
    assert add_two_numbers(-1.7976931348623157e+308, -1.7976931348623157e+308) == -3.5953862697246314e+308, "Test case 12 failed"

def test_add_safety_contract_precondition_a():
    with pytest.raises(AssertionError):
        add_two_numbers("a", 2)

def test_add_safety_contract_precondition_b():
    with pytest.raises(AssertionError):
        add_two_numbers(2, "b")

def test_add_safety_contract_postcondition():
    result = add_two_numbers(1.5, 2.5)
    assert isinstance(result, (int, float)), "Post-condition: Return value is a number"