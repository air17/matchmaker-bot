import google_sheets as gs

print("Testing sync_questions()...")
questions = gs.sync_questions()
print(f"Total questions found: {len(questions)}")
for i, q in enumerate(questions):
    print(f"Question {i + 1}: {q['text']}")
    print(f"  Options: {q['options']}")
