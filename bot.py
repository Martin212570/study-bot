import os, sqlite3
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import discord
from discord.ext import commands, tasks
from discord import app_commands

# โหลดตัวแปรจากไฟล์ .env
load_dotenv()

DB = "study_tasks.db"
STATUS = {"todo": "⚪ ยังไม่เริ่ม", "doing": "🔵 กำลังทำ", "review": "🟡 รอตรวจ", "done": "🟢 เสร็จแล้ว"}
PRIORITY = {"low": "🟢 ต่ำ", "medium": "🟡 ปานกลาง", "high": "🔴 สูง"}

# ✅ สร้าง bot instance
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    c = conn()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS subjects(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, emoji TEXT DEFAULT '📚');
    CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY AUTOINCREMENT, subject_id INTEGER NOT NULL, title TEXT NOT NULL, description TEXT DEFAULT '', assignee_id INTEGER, due_at TEXT, priority TEXT DEFAULT 'medium', link TEXT DEFAULT '', status TEXT DEFAULT 'todo', created_by INTEGER, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS settings(guild_id INTEGER NOT NULL, key TEXT NOT NULL, value TEXT, PRIMARY KEY(guild_id, key));
    CREATE TABLE IF NOT EXISTS reminders(task_id INTEGER NOT NULL, kind TEXT NOT NULL, sent_on TEXT NOT NULL, PRIMARY KEY(task_id, kind, sent_on));
    """)
    c.commit()
    c.close()

def get_subjects():
    c = conn()
    r = c.execute("SELECT * FROM subjects ORDER BY name").fetchall()
    c.close()
    return r

def get_subject(sid):
    c = conn()
    r = c.execute("SELECT * FROM subjects WHERE id=?", (sid,)).fetchone()
    c.close()
    return r

def get_task(tid):
    c = conn()
    r = c.execute("""SELECT t.*, s.name subject_name, s.emoji FROM tasks t JOIN subjects s ON s.id=t.subject_id WHERE t.id=?""", (tid,)).fetchone()
    c.close()
    return r

def get_tasks(sid):
    c = conn()
    r = c.execute("SELECT * FROM tasks WHERE subject_id=? ORDER BY CASE status WHEN 'todo' THEN 1 WHEN 'doing' THEN 2 WHEN 'review' THEN 3 ELSE 4 END, due_at", (sid,)).fetchall()
    c.close()
    return r

def set_setting(gid, key, value):
    c = conn()
    c.execute("INSERT OR REPLACE INTO settings VALUES(?,?,?)", (gid, key, str(value)))
    c.commit()
    c.close()

def get_setting(gid, key):
    c = conn()
    r = c.execute("SELECT value FROM settings WHERE guild_id=? AND key=?", (gid, key)).fetchone()
    c.close()
    return r["value"] if r else None

def stats(sid):
    rows = get_tasks(sid)
    d = {k: sum(1 for r in rows if r["status"] == k) for k in STATUS}
    total = len(rows)
    done = d["done"]
    return d, total, round(done / total * 100) if total else 0

def board_embed(sid):
    s = get_subject(sid)
    d, total, p = stats(sid)
    e = discord.Embed(title=f"{s['emoji']} {s['name']}", description=f"**{total} งาน** • 🟢 เสร็จแล้ว **{d['done']}** • ความสำเร็จ **{p}%**", color=discord.Color.blurple())
    rows = get_tasks(sid)
    for st in STATUS:
        rs = [r for r in rows if r["status"] == st]
        lines = []
        for r in rs[:8]:
            who = f" <@{r['assignee_id']}>" if r["assignee_id"] else ""
            due = f" • 📅 {r['due_at']}" if r["due_at"] else ""
            lines.append(f"**#{r['id']}** {r['title']}{who}{due}")
        if len(rs) > 8:
            lines.append(f"…และอีก {len(rs)-8} งาน")
        e.add_field(name=STATUS[st], value="\n".join(lines) or "—", inline=True)
    return e

def home_embed():
    return discord.Embed(title="📚 STUDY TASK BOARD", description="เลือกวิชาที่ต้องการดูจากเมนูด้านล่าง", color=discord.Color.blurple())

async def send_log(guild, text):
    cid = get_setting(guild.id, "notify_channel")
    if cid:
        ch = guild.get_channel(int(cid))
        if ch:
            try:
                await ch.send(text)
            except discord.HTTPException:
                pass

class SubjectSelect(discord.ui.Select):
    def __init__(self):
        opts = [discord.SelectOption(label=s["name"][:100], value=str(s["id"]), emoji=s["emoji"]) for s in get_subjects()[:25]]
        super().__init__(placeholder="📚 เลือกวิชา", options=opts or [discord.SelectOption(label="ยังไม่มีวิชา", value="0")])
    async def callback(self, i):
        if self.values[0] == "0":
            return await i.response.send_message("ยังไม่มีวิชา ให้สร้างด้วยปุ่ม ⚙️ จัดการวิชา", ephemeral=True)
        sid = int(self.values[0])
        await i.response.edit_message(embed=board_embed(sid), view=BoardView(sid))

class HomeView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)
        self.add_item(SubjectSelect())
        self.add_item(ManageSubjectsButton())

class ManageSubjectsButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="จัดการวิชา", emoji="⚙️", style=discord.ButtonStyle.secondary, row=1)
    async def callback(self, i):
        await i.response.send_message("จัดการวิชา", view=SubjectManageView(), ephemeral=True)

class SubjectManageView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(AddSubjectButton())
        self.add_item(DeleteSubjectButton())
        self.add_item(RenameSubjectButton())

class AddSubjectButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="เพิ่มวิชา", emoji="➕", style=discord.ButtonStyle.success)
    async def callback(self, i):
        await i.response.send_modal(SubjectModal())

class SubjectModal(discord.ui.Modal, title="เพิ่มวิชา"):
    name = discord.ui.TextInput(label="ชื่อวิชา", placeholder="เช่น Database", max_length=80)
    emoji = discord.ui.TextInput(label="Emoji", default="📚", required=False, max_length=2)
    async def on_submit(self, i):
        c = conn()
        try:
            c.execute("INSERT INTO subjects(name, emoji) VALUES(?,?)", (self.name.value, self.emoji.value or "📚"))
            c.commit()
            await i.response.send_message(f"✅ เพิ่มวิชา **{self.name.value}** แล้ว", ephemeral=True)
        except sqlite3.IntegrityError:
            await i.response.send_message("❌ มีวิชานี้อยู่แล้ว", ephemeral=True)
        finally:
            c.close()

class DeleteSubjectButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="ลบวิชา", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def callback(self, i):
        rows = get_subjects()
        if not rows:
            return await i.response.send_message("ยังไม่มีวิชา", ephemeral=True)
        await i.response.send_message("เลือกวิชาที่จะลบ", view=DeleteSubjectView(), ephemeral=True)

class DeleteSubjectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        opts = [discord.SelectOption(label=s["name"], value=str(s["id"]), emoji=s["emoji"]) for s in get_subjects()[:25]]
        self.add_item(DeleteSelect(opts))

class DeleteSelect(discord.ui.Select):
    def __init__(self, opts):
        super().__init__(placeholder="เลือกวิชา", options=opts)
    async def callback(self, i):
        sid = int(self.values[0])
        s = get_subject(sid)
        c = conn()
        c.execute("DELETE FROM tasks WHERE subject_id=?", (sid,))
        c.execute("DELETE FROM subjects WHERE id=?", (sid,))
        c.commit()
        c.close()
        await i.response.send_message(f"🗑️ ลบ **{s['name']}** และงานในวิชานี้แล้ว", ephemeral=True)

class RenameSubjectButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="เปลี่ยนชื่อวิชา", emoji="✏️", style=discord.ButtonStyle.secondary)
    async def callback(self, i):
        await i.response.send_message("ฟีเจอร์เปลี่ยนชื่อจะทำในรอบถัดไป", ephemeral=True)

class BoardView(discord.ui.View):
    def __init__(self, sid):
        super().__init__(timeout=600)
        self.sid = sid
        self.add_item(AddTaskButton(sid))
        self.add_item(StatusTaskButton(sid))
        self.add_item(MyTasksButton(sid))
        self.add_item(StatsButton(sid))
        self.add_item(RefreshButton(sid))
        self.add_item(BackButton())

class AddTaskButton(discord.ui.Button):
    def __init__(self, sid):
        super().__init__(label="เพิ่มงาน", emoji="➕", style=discord.ButtonStyle.success)
        self.sid = sid
    async def callback(self, i):
        await i.response.send_modal(TaskModal(self.sid))

class TaskModal(discord.ui.Modal, title="เพิ่มงาน"):
    title_in = discord.ui.TextInput(label="ชื่องาน", placeholder="เช่น ทำ ER Diagram", max_length=150)
    desc = discord.ui.TextInput(label="รายละเอียด", required=False, style=discord.TextStyle.paragraph, max_length=1000)
    due = discord.ui.TextInput(label="กำหนดส่ง (YYYY-MM-DD HH:MM)", required=False, placeholder="2026-09-10 23:59")
    priority = discord.ui.TextInput(label="ความสำคัญ: low / medium / high", default="medium", required=False)
    link = discord.ui.TextInput(label="ไฟล์ / ลิงก์", required=False, max_length=500)
    def __init__(self, sid):
        super().__init__()
        self.sid = sid
    async def on_submit(self, i):
        p = self.priority.value if self.priority.value in PRIORITY else "medium"
        c = conn()
        cur = c.execute("INSERT INTO tasks(subject_id, title, description, assignee_id, due_at, priority, status, created_by, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                        (self.sid, self.title_in.value, self.desc.value, i.user.id, self.due.value, p, "todo", i.user.id, datetime.now(timezone.utc).isoformat()))
        tid = cur.lastrowid
        c.commit()
        c.close()
        s = get_subject(self.sid)
        await i.response.edit_message(embed=board_embed(self.sid), view=BoardView(self.sid))
        await send_log(i.guild, f"📌 **เพิ่มงานใหม่**\n{s['emoji']} {s['name']} — **#{tid} {self.title_in.value}**\n👤 {i.user.mention}\n⚪ ยังไม่เริ่ม")

class StatusTaskButton(discord.ui.Button):
    def __init__(self, sid):
        super().__init__(label="อัปเดตสถานะ", emoji="🔄", style=discord.ButtonStyle.primary)
        self.sid = sid
    async def callback(self, i):
        rows = get_tasks(self.sid)
        if not rows:
            return await i.response.send_message("วิชานี้ยังไม่มีงาน", ephemeral=True)
        await i.response.send_message("เลือกงาน", view=TaskSelectView(self.sid, rows), ephemeral=True)

class TaskSelectView(discord.ui.View):
    def __init__(self, sid, rows):
        super().__init__(timeout=300)
        self.sid = sid
        self.add_item(TaskSelect(sid, rows))

class TaskSelect(discord.ui.Select):
    def __init__(self, sid, rows):
        opts = [discord.SelectOption(label=f"#{r['id']} {r['title']}"[:100], value=str(r["id"]), description=STATUS[r["status"]][:100]) for r in rows[:25]]
        super().__init__(placeholder="เลือกงาน", options=opts)
        self.sid = sid
    async def callback(self, i):
        await i.response.edit_message(content="เลือกสถานะใหม่", view=StatusSelectView(int(self.values[0]), self.sid))

class StatusSelectView(discord.ui.View):
    def __init__(self, tid, sid):
        super().__init__(timeout=300)
        self.add_item(StatusSelect(tid, sid))

class StatusSelect(discord.ui.Select):
    def __init__(self, tid, sid):
        super().__init__(placeholder="เลือกสถานะ", options=[discord.SelectOption(label=v, value=k) for k,v in STATUS.items()])
        self.tid = tid
        self.sid = sid
    async def callback(self, i):
        r = get_task(self.tid)
        old = r["status"]
        new = self.values[0]
        c = conn()
        c.execute("UPDATE tasks SET status=? WHERE id=?", (new, self.tid))
        c.commit()
        c.close()
        await i.response.edit_message(content=f"✅ **#{self.tid} {r['title']}**\n{STATUS[old]} → {STATUS[new]}", view=None)
        await send_log(i.guild, f"🔄 **อัปเดตสถานะ**\n{r['emoji']} {r['subject_name']} — **#{r['id']} {r['title']}**\n{STATUS[old]} → {STATUS[new]}\n👤 {i.user.mention}")

class MyTasksButton(discord.ui.Button):
    def __init__(self, sid):
        super().__init__(label="งานของฉัน", emoji="👤", style=discord.ButtonStyle.secondary)
        self.sid = sid
    async def callback(self, i):
        c = conn()
        rows = c.execute("SELECT * FROM tasks WHERE subject_id=? AND assignee_id=? ORDER BY due_at", (self.sid, i.user.id)).fetchall()
        c.close()
        e = discord.Embed(title="👤 งานของฉัน", color=discord.Color.blurple())
        e.description = "\n".join(f"**#{r['id']}** {r['title']} — {STATUS[r['status']]}" for r in rows) or "ยังไม่มีงานที่มอบหมายให้คุณ"
        await i.response.send_message(embed=e, ephemeral=True)

class StatsButton(discord.ui.Button):
    def __init__(self, sid):
        super().__init__(label="สถิติ", emoji="📊", style=discord.ButtonStyle.secondary)
        self.sid = sid
    async def callback(self, i):
        d, total, p = stats(self.sid)
        s = get_subject(self.sid)
        e = discord.Embed(title=f"{s['emoji']} {s['name']} — สถิติ", description=f"ทั้งหมด **{total}** งาน\n⚪ {d['todo']} • 🔵 {d['doing']} • 🟡 {d['review']} • 🟢 {d['done']}\n\n**ความสำเร็จ {p}%**", color=discord.Color.blurple())
        await i.response.send_message(embed=e, ephemeral=True)

class RefreshButton(discord.ui.Button):
    def __init__(self, sid):
        super().__init__(label="รีเฟรช", emoji="🔄", style=discord.ButtonStyle.secondary)
        self.sid = sid
    async def callback(self, i):
        await i.response.edit_message(embed=board_embed(self.sid), view=BoardView(self.sid))

class BackButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="เลือกวิชา", emoji="📚", style=discord.ButtonStyle.secondary)
    async def callback(self, i):
        await i.response.edit_message(embed=home_embed(), view=HomeView())

@bot.tree.command(name="board", description="เปิด Study Task Board")
async def board(i: discord.Interaction):
    await i.response.send_message(embed=home_embed(), view=HomeView())

@bot.command()
async def notifychannel(ctx):
    set_setting(ctx.guild.id, "notify_channel", ctx.channel.id)
    await ctx.send("✅ ตั้งห้องนี้เป็นห้องแจ้งเตือนแล้ว")

@bot.event
async def on_ready():
    init_db()
    await bot.tree.sync()
    if not reminder_loop.is_running():
        reminder_loop.start()
    print(f"Logged in as {bot.user}")

@tasks.loop(minutes=15)
async def reminder_loop():
    now = datetime.now(timezone.utc)
    c = conn()
    rows = c.execute("""SELECT t.*, s.name subject_name, s.emoji FROM tasks t JOIN subjects s ON s.id=t.subject_id WHERE t.status!='done' AND t.due_at IS NOT NULL AND t.due_at!=''""").fetchall()
    for r in rows:
        try:
            due = datetime.strptime(r["due_at"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        hours = (due - now).total_seconds() / 3600
        kind = "due" if 0 <= hours <= 0.5 else ("soon" if 0.5 < hours <= 24 else None)
        if not kind:
            continue
        day = due.date().isoformat()
        if c.execute("SELECT 1 FROM reminders WHERE task_id=? AND kind=? AND sent_on=?", (r["id"], kind, day)).fetchone():
            continue
        c.execute("INSERT INTO reminders VALUES(?,?,?)", (r["id"], kind, day))
        c.commit()
        for g in bot.guilds:
            cid = get_setting(g.id, "notify_channel")
            if cid:
                ch = g.get_channel(int(cid))
                if ch:
                    prefix = "🚨 **ถึงกำหนดส่งแล้ว!**" if kind == "due" else "⏰ **งานใกล้ถึงกำหนด!**"
                    msg_text = f"{prefix}\n{r['emoji']} **{r['subject_name']}** — #{r['id']} {r['title']}\n📅 {r['due_at']} UTC"
                    if r["assignee_id"]:
                        msg_text += f"\n👤 <@{r['assignee_id']}>"
                    try:
                        await ch.send(msg_text)
                    except discord.HTTPException:
                        pass
    c.close()

# ✅ เริ่มต้นแพลตฟอร์ม
if __name__ == "__main__":
    init_db()
    keep_alive()
    bot.run(os.getenv("DISCORD_TOKEN"))
