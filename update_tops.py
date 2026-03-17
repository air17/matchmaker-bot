import google_sheets as gs
import asyncio


async def update_all_tops():
    print("Starting update of all tops...")
    try:
        await gs.recalculate_top_matches()
        print("Successfully updated all top matches (Summary and Personal sheets).")
    except Exception as e:
        print(f"Error during update: {e}")


if __name__ == "__main__":
    asyncio.run(update_all_tops())
