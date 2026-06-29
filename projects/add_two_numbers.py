def add_two_numbers(a: float, b: float) -> float:
    """
    Adds two numbers and returns the result.

    Parameters:
    a (float): The first number to be added.
    b (float): The second number to be added.

    Returns:
    float: The sum of the two input numbers.

    Safety Contracts:
    - Pre-condition: 'a' is a number
    - Post-condition: Return value is a number
    """
    assert isinstance(a, (int, float)), "Input 'a' must be a number"
    assert isinstance(b, (int, float)), "Input 'b' must be a number"

    return a + b

if __name__ == "__main__":
    a = float(input("Enter the first number: "))
    b = float(input("Enter the second number: "))
    result = add_two_numbers(a, b)
    print(f"The sum of {a} and {b} is {result}")