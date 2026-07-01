# filepath: sandbox/queue_data_structure.py

class Queue:
    def __init__(self):
        """
        Initializes an empty queue.
        """
        self.items = []

    def enqueue(self, element: int) -> None:
        """
        Adds a non-negative integer to the end of the queue.
        
        Parameters:
        - element (int): The non-negative integer to be added. Raises ValueError if negative.

        Returns:
        None
        """
        assert isinstance(element, int), "Element must be an integer."
        assert element >= 0, "Element must be non-negative."
        self.items.append(element)

    def dequeue(self) -> int:
        """
        Removes and returns an element from the front of the queue.
        
        Returns:
        - int: The removed element. Raises IndexError if the queue is empty.

        """
        if not self.items:
            raise IndexError("Queue is empty.")
        return self.items.pop(0)

    def size(self) -> int:
        """
        Returns the number of elements in the queue.
        
        Returns:
        - int: The current number of elements in the queue.
        """
        return len(self.items)