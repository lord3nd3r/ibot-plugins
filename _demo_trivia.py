#!/usr/bin/env python3
"""Demo script showing hint generation and categories."""
from trivia_game import TriviaGame
import json
import os

# Load questions from the same directory as this script
script_dir = os.path.dirname(os.path.abspath(__file__))
questions_path = os.path.join(script_dir, "questions.json")
game = TriviaGame.load_from_file(questions_path)

print("=" * 60)
print("TRIVIA GAME FRAMEWORK - DEMO")
print("=" * 60)

# Show categories
categories = {}
for q in game.questions:
    cat = q.get("category", "General")
    categories[cat] = categories.get(cat, 0) + 1

print(f"\n📊 Total Questions: {len(game.questions)}")
print(f"📁 Total Categories: {len(categories)}")
print("\nCategories:")
for cat, count in sorted(categories.items(), key=lambda x: -x[1])[:15]:
    print(f"  • {cat}: {count} questions")

# Demo hint generation
print("\n" + "=" * 60)
print("PROGRESSIVE HINT DEMO")
print("=" * 60)

examples = [
    "israel and jordan",
    "george washington",
    "lyndon johnson",
    "adze",
    "ranee"
]

for answer in examples:
    hints = game.generate_hints(answer, num_hints=3)
    print(f"\nAnswer: '{answer}'")
    for i, hint in enumerate(hints, 1):
        print(f"  Hint {i}: {hint}")

print("\n" + "=" * 60)
print("SAMPLE QUESTIONS BY CATEGORY")
print("=" * 60)

sample_cats = ["Geography", "History", "Science", "Movies", "Sports"]
for cat in sample_cats:
    cat_questions = [q for q in game.questions if q.get("category") == cat]
    if cat_questions:
        print(f"\n{cat} (showing 2/{len(cat_questions)}):")
        for q in cat_questions[:2]:
            print(f"  Q: {q['question']}")
            print(f"  A: {game.get_answer_text(q)}")

print("\n" + "=" * 60)
print("✓ Framework ready! Run with:")
print("  python3 _cli_trivia.py -i --num 10 --shuffle")
print("=" * 60)
