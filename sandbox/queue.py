class Queue:
    """
    A Queue data structure class with enqueue, dequeue, and size methods.
    """

    def __init__(self):
        """
        Initializes an empty queue.
        """
        self.elements = []

    def enqueue(self, element: int) -> None:
        """
        Adds a non-negative integer element to the end of the queue.

        Args:
            element (int): The element to be added to the queue.

        Raises:
            ValueError: If the element is not a non-negative integer.
        """
        if not isinstance(element, int) or element < 0:
            raise ValueError("element must be a non-negative integer")
        self.elements.append(element)

    def dequeue(self) -> int | None:
        """
        Removes and returns the front element of the queue, or None if the queue is empty.

        Returns:
            int | None: The front element of the queue, or None if the queue is empty.
        """
        if not self.elements:
            return None
        return self.elements.pop(0)

    def size(self) -> int:
        """
        Returns the number of elements in the queue.

        Returns:
            int: The number of elements in the queue.
        """
        return len(self.elements)


# Example usage:
if __name__ == "__main__":
    queue = Queue()
    queue.enqueue(5)
    print(queue.size())  # Output: 1
    print(queue.dequeue())  # Output: 5
    print(queue.size())  # Output: 0
    print(queue.dequeue())  # Output: None