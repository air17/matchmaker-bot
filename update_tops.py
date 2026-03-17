import google_sheets as gs


def update_all_tops():
    print("Starting update of all tops...")
    try:
        gs.recalculate_top_matches()
        print("Successfully updated all top matches (Summary and Personal sheets).")
    except Exception as e:
        print(f"Error during update: {e}")


if __name__ == "__main__":
    update_all_tops()
