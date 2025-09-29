import aiosqlite
import asyncio

DB_FILE = "tasks.db"

async def print_all_tasks():
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM tasks ORDER BY created_at") as cur:
            rows = await cur.fetchall()
            if not rows:
                print("База пуста 😅")
                return
            current_user = None
            for row in rows:
                if row["user_id"] != current_user:
                    current_user = row["user_id"]
                    print(f"\nПользователь {current_user}:")
                print(f"  [{row['id']}] {row['task']} (создано: {row['created_at']})")

# Запуск
asyncio.run(print_all_tasks())
