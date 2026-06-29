class Ball:
    """
    This class represents a ball in the match and has a method to update the score.

    Attributes:
        runs_scored (int): The number of runs scored on this ball.
    """

    def __init__(self, runs_scored: int):
        """
        Initializes a Ball object with the given runs scored.

        Args:
            runs_scored (int): The number of runs scored on this ball.

        Raises:
            AssertionError: If runs_scored is a negative integer.
        """
        assert isinstance(runs_scored, int) and runs_scored >= 0, "Invalid input: runs_scored must be a non-negative integer"
        self.runs_scored = runs_scored

    def get_runs_scored(self) -> int:
        """
        Returns the number of runs scored on this ball.

        Returns:
            int: The number of runs scored on this ball.
        """
        return self.runs_scored


class CricketMatch:
    """
    This class represents a cricket match and has methods to update the score and calculate the total score.

    Attributes:
        overs (int): The number of overs in the match.
        balls_per_over (int): The number of balls per over.
        runs_per_ball (int): The number of runs scored per ball.
        total_score (int): The total score of the match.
    """

    def __init__(self, overs: int, balls_per_over: int, runs_per_ball: int):
        """
        Initializes a CricketMatch object with the given overs, balls per over, and runs per ball.

        Args:
            overs (int): The number of overs in the match.
            balls_per_over (int): The number of balls per over.
            runs_per_ball (int): The number of runs scored per ball.

        Raises:
            AssertionError: If overs or balls_per_over is not a positive integer, or if runs_per_ball is a negative integer.
        """
        assert isinstance(overs, int) and overs > 0, "Invalid input: overs must be a positive integer"
        assert isinstance(balls_per_over, int) and balls_per_over > 0, "Invalid input: balls_per_over must be a positive integer"
        assert isinstance(runs_per_ball, int) and runs_per_ball >= 0, "Invalid input: runs_per_ball must be a non-negative integer"
        self.overs = overs
        self.balls_per_over = balls_per_over
        self.runs_per_ball = runs_per_ball
        self.total_score = 0

    def update_score(self, runs_scored: int) -> int:
        """
        Updates the score of the match.

        Args:
            runs_scored (int): The number of runs scored.

        Returns:
            int: The updated score.

        Raises:
            AssertionError: If runs_scored is a negative integer.
        """
        assert isinstance(runs_scored, int) and runs_scored >= 0, "Invalid input: runs_scored must be a non-negative integer"
        self.total_score += runs_scored
        return self.total_score

    def calculate_total_score(self) -> int:
        """
        Calculates the total score of the match.

        Returns:
            int: The total score of the match.
        """
        return self.overs * self.balls_per_over * self.runs_per_ball

    def display_score(self, score: int) -> None:
        """
        Displays the score of the match.

        Args:
            score (int): The score to display.

        Raises:
            AssertionError: If score is a negative integer.
        """
        assert isinstance(score, int) and score >= 0, "Invalid input: score must be a non-negative integer"
        print(f"The current score is: {score}")


def implementation(overs: int, balls_per_over: int, runs_per_ball: int) -> None:
    """
    This function implements the cricket match score counter.

    It creates a CricketMatch object, updates the score, calculates the total score, and displays the score.
    """
    match = CricketMatch(overs, balls_per_over, runs_per_ball)
    for over in range(match.overs):
        for ball in range(match.balls_per_over):
            ball_obj = Ball(match.runs_per_ball)
            updated_score = match.update_score(ball_obj.get_runs_scored())
            match.display_score(updated_score)


if __name__ == "__main__":
    overs = int(input("Enter the number of overs: "))
    balls_per_over = int(input("Enter the number of balls per over: "))
    runs_per_ball = int(input("Enter the number of runs scored per ball: "))
    implementation(overs, balls_per_over, runs_per_ball)