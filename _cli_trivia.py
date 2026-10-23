#!/usr/bin/env python3
"""CLI Trivia runner with IRC-bot-style output (timed hints, progressive masking)."""
import argparse
import sys
import time
from typing import Any, Optional

from trivia_game import TriviaGame


def ask_with_hints(game: TriviaGame, question: dict, hint_delay: float = 10.0, inactivity_limit: int = 5) -> Optional[str]:
    """
    Display question and progressive hints. In actual bot usage, you'd check for user input.
    For CLI demo, we simulate timeout behavior.
    
    Returns: answer string if user responded, None if timed out
    """
    q_num = game.current
    category = question.get("category", "")
    q_text = question.get("question", "")
    
    # Format question line
    if category:
        print(f"{q_num}. {category}: {q_text}")
    else:
        print(f"{q_num}. {q_text}")
    
    # Generate hints
    answer = game.get_answer_text(question)
    hints = game.generate_hints(answer, num_hints=3)
    
    # Display hints with delays (in real bot, you'd check for user input between hints)
    for hint in hints:
        time.sleep(hint_delay)
        print(f"Hint: {hint}")
    
    # Final delay before timeout
    time.sleep(hint_delay)
    
    # Simulate timeout (in real usage, check if anyone answered)
    print(f"Time's up! The answer was: {answer}")
    game.check_answer(question, None)  # No one answered
    return None


def ask_interactive(game: TriviaGame, question: dict) -> Optional[str]:
    """Interactive version: actually prompt user for answer."""
    q_num = game.current
    category = question.get("category", "")
    q_text = question.get("question", "")
    
    print()
    if category:
        print(f"{q_num}. {category}: {q_text}")
    else:
        print(f"{q_num}. {q_text}")
    
    choices = question.get("choices")
    if choices:
        for i, c in enumerate(choices, start=1):
            print(f"  {i}) {c}")
    
    answer = game.get_answer_text(question)
    hints = game.generate_hints(answer, num_hints=3)
    
    print(f"\nHints: {' -> '.join(hints)}")
    print(f"Answer: {answer}\n")
    
    raw = input("Your answer (or Enter to skip): ").strip()
    if raw == "":
        return None
    if raw.isdigit() and choices:
        return int(raw) - 1
    return raw


def run():
    p = argparse.ArgumentParser(description="CLI trivia runner with IRC-bot-style output")
    p.add_argument("--file", "-f", default="questions.json", help="questions JSON file")
    p.add_argument("--num", "-n", type=int, default=10, help="number of questions to ask")
    p.add_argument("--shuffle", "-s", action="store_true", help="shuffle questions")
    p.add_argument("--category", "-c", help="filter by category")
    p.add_argument("--interactive", "-i", action="store_true", help="interactive mode (show answer immediately)")
    p.add_argument("--hint-delay", "-d", type=float, default=5.0, help="seconds between hints (demo mode)")
    p.add_argument("--inactivity-limit", type=int, default=5, help="stop after N unanswered questions")
    args = p.parse_args()

    try:
        game = TriviaGame.load_from_file(args.file)
    except FileNotFoundError:
        print(f"Questions file not found: {args.file}")
        sys.exit(2)
    
    # Filter by category if requested
    if args.category:
        filtered = [q for q in game.questions if q.get("category", "").lower() == args.category.lower()]
        if not filtered:
            print(f"No questions found for category: {args.category}")
            sys.exit(1)
        game.questions = filtered
    
    if args.shuffle:
        game.shuffle()

    print(f"Starting trivia! {len(game.questions)} questions loaded.")
    if args.category:
        print(f"Category: {args.category}")
    print()

    asked = 0
    unanswered_count = 0
    
    while asked < args.num:
        q = game.next_question()
        if q is None:
            break
        
        if args.interactive:
            result = ask_interactive(game, q)
            if result is not None:
                correct = game.check_answer(q, result, winner_name="Player")
                if correct:
                    print(f"Correct! Streak: {game.streak}")
                    unanswered_count = 0
                else:
                    answer = game.get_answer_text(q)
                    print(f"Wrong — correct: {answer}")
                    unanswered_count += 1
            else:
                unanswered_count += 1
        else:
            # Demo mode: show hints with delays, then timeout
            ask_with_hints(game, q, hint_delay=args.hint_delay, inactivity_limit=args.inactivity_limit)
            unanswered_count += 1
        
        asked += 1
        
        # Check inactivity limit
        if unanswered_count >= args.inactivity_limit:
            print(f"\nTrivia stopped due to inactivity ({args.inactivity_limit} unanswered questions).")
            break
    
    print()
    print(f"Trivia ended. Score: {game.score} / {asked}")


if __name__ == "__main__":
    run()
