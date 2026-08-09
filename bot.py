import asyncio
import datetime
from datetime import datetime, timedelta
import json
import os
import threading
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from flask import Flask

# --- KONFIGURACJA PROXY I USER-AGENT ---
proxy_url = os.environ.get("HTTP_PROXY")
if proxy_url:
    print(f"🌐 [PROXY] Konfiguracja proxy dla bota: {proxy_url}")

CUSTOM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# --- SERWER WWW (FLASK) ---
app = Flask(__name__)
bot_status = "Uruchamianie..."

@app.route('/')
def home():
    return f"Status bota: {bot_status}"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# --- KONFIGURACJA BOTA ---
intents = discord.Intents.all()

class MyBot(commands.Bot):
    def __init__(self):
        kwargs = {"command_prefix": "!", "intents": intents}
        if proxy_url:
            kwargs["proxy"] = proxy_url
        super().__init__(**kwargs)

    async def setup_hook(self):
        self.add_view(VerifyView())
        self.add_view(TicketView())
        self.add_view(KlepaView())
        self.add_view(PodanieTicketView())
        try:
            await self.tree.sync()
            print("⚡ Zsynchronizowano komendy globalnie!")
        except Exception as e:
            print(f"⚠️ Błąd podczas synchronizacji komend: {e}")

bot = MyBot()

ARCHIVE_FILE = "ticket_archive.json"
CONFIG_FILE = "server_config.json"

def load_archive():
    if os.path.exists(ARCHIVE_FILE):
        try:
            with open(ARCHIVE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_archive(archive_data):
    try:
        with open(ARCHIVE_FILE, "w", encoding="utf-8") as f:
            json.dump(archive_data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"⚠️ [ARCHIWUM] Błąd zapisu: {e}")

def load_config():
    default_config = {
        "regulamin_title": "📜 REGULAMIN GILDII",
        "regulamin_content": (
            "**I. POSTANOWIENIA OGÓLNE**\n"
            "→ 1. Dołączając na serwer, akceptujesz poniższy regulamin.\n"
            "→ 2. Nieznajomość regulaminu nie zwalnia z jego przestrzegania.\n"
            "→ 3. Wspólna gra, szacunek i aktywność to klucz do sukcesu.\n\n"
            "**II. ZASADY KULTURY**\n"
            "→ 1. Szanujemy się nawzajem."
        ),
        "pytania": [
            "1. Nick z Minecrafta",
            "2. Ile masz lat?",
            "3. Podaj 3 ostatnie gildie",
            "4. Dlaczego my?",
            "5. Czy znasz kogoś?"
        ]
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k, v in default_config.items():
                    if k not in data:
                        data[k] = v
                return data
        except Exception:
            return default_config
    return default_config

def save_config(config_data):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config_data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"⚠️ [CONFIG] Błąd zapisu: {e}")

async def get_transcript_text(channel: discord.TextChannel):
    transcript_lines = []
    async for message in channel.history(limit=100, oldest_first=True):
        if message.embeds:
            for embed in message.embeds:
                if embed.title and "PODANIE REKRUTACYJNE" in embed.title:
                    transcript_lines.append(f"📋 **{embed.title}**")
                    if embed.description:
                        transcript_lines.append(embed.description)
                    for field in embed.fields:
                        transcript_lines.append(f"• **{field.name}** {field.value}")
                    transcript_lines.append("-" * 30)

        if not message.author.bot and message.content:
            transcript_lines.append(f"**{message.author.display_name}**: {message.content}")

    transcript_text = "\n".join(transcript_lines) if transcript_lines else "Brak treści do wyświetlenia."
    if len(transcript_text) > 4000:
        transcript_text = "...(historia zbyt długa)...\n" + transcript_text[-4000:]
    return transcript_text

async def send_log(guild, message):
    log_channel = discord.utils.get(guild.text_channels, name="📑-logi")
    if log_channel:
        embed = discord.Embed(
            title="⚙️ SYSTEM LOGS",
            description=message,
            color=discord.Color.dark_grey(),
            timestamp=datetime.now()
        )
        await log_channel.send(embed=embed)

def has_management_permission(member: discord.Member) -> bool:
    if member == member.guild.owner or member.guild_permissions.administrator:
        return True
    allowed_keywords = ["szef", "zarząd", "rekruter"]
    for role in member.roles:
        if any(keyword in role.name.lower() for keyword in allowed_keywords):
            return True
    return False

@tasks.loop(minutes=10)
async def keep_alive_ping():
    url = os.environ.get("RENDER_EXTERNAL_URL")
    if url:
        try:
            async with aiohttp.ClientSession(headers=CUSTOM_HEADERS) as session:
                async with session.get(url, proxy=proxy_url if proxy_url else None) as response:
                    if response.status == 200:
                        print(f"⏰ [KEEP-ALIVE] Ping udany do {url}")
                    else:
                        print(f"⚠️ [KEEP-ALIVE] Otrzymano status HTTP {response.status}")
        except Exception as e:
            print(f"⚠️ [KEEP-ALIVE] Błąd połączenia: {e}")

def get_ticket_target(channel: discord.TextChannel, moderator: discord.Member):
    for obj, overwrite in channel.overwrites.items():
        if isinstance(obj, discord.Member) and not obj.bot and obj != moderator:
            return obj
    return None

# --- MODALE & WIDOKI ---

class PodanieModal(discord.ui.Modal, title="📝 Formularz Podania"):
    def __init__(self):
        super().__init__()
        cfg = load_config()
        pytania = cfg.get("pytania", ["1. Nick", "2. Wiek", "3. Gildie", "4. Dlaczego my?", "5. Znajomi"])
        self.question_inputs = []
        for i, q_text in enumerate(pytania[:5]):
            style = discord.TextStyle.paragraph if i in [2, 3] else discord.TextStyle.short
            text_input = discord.ui.TextInput(
                label=q_text[:45],
                placeholder="Wpisz odpowiedź...",
                style=style,
                required=True,
                max_length=300
            )
            self.question_inputs.append(text_input)
            self.add_item(text_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            if self.question_inputs:
                await interaction.user.edit(nick=self.question_inputs[0].value.strip()[:32])
        except Exception:
            pass

        cfg = load_config()
        pytania = cfg.get("pytania", [])
        embed = discord.Embed(
            title=f"📋 PODANIE REKRUTACYJNE — {interaction.user.display_name}",
            description=f"**Kandydat:** {interaction.user.mention}",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)

        for idx, text_input in enumerate(self.question_inputs):
            q_label = pytania[idx] if idx < len(pytania) else f"Pytanie {idx+1}"
            embed.add_field(name=f"➞ {q_label} »", value=text_input.value, inline=False)

        await interaction.channel.send(embed=embed)
        await interaction.response.send_message("✅ Podanie zostało wysłane!", ephemeral=True)
        await send_log(interaction.guild, f"📝 **NOWE PODANIE:** Użytkownik {interaction.user.mention} wypełnił podanie.")

class PytaniaModal(discord.ui.Modal, title="⚙️ Konfiguracja Pytań Podania"):
    p1 = discord.ui.TextInput(label="Pytanie 1", style=discord.TextStyle.short, required=True, max_length=45)
    p2 = discord.ui.TextInput(label="Pytanie 2", style=discord.TextStyle.short, required=True, max_length=45)
    p3 = discord.ui.TextInput(label="Pytanie 3", style=discord.TextStyle.short, required=True, max_length=45)
    p4 = discord.ui.TextInput(label="Pytanie 4", style=discord.TextStyle.short, required=True, max_length=45)
    p5 = discord.ui.TextInput(label="Pytanie 5", style=discord.TextStyle.short, required=True, max_length=45)

    def __init__(self):
        super().__init__()
        cfg = load_config()
        pytania = cfg.get("pytania", ["", "", "", "", ""])
        if len(pytania) > 0: self.p1.default = pytania[0]
        if len(pytania) > 1: self.p2.default = pytania[1]
        if len(pytania) > 2: self.p3.default = pytania[2]
        if len(pytania) > 3: self.p4.default = pytania[3]
        if len(pytania) > 4: self.p5.default = pytania[4]

    async def on_submit(self, interaction: discord.Interaction):
        cfg = load_config()
        cfg["pytania"] = [self.p1.value, self.p2.value, self.p3.value, self.p4.value, self.p5.value]
        save_config(cfg)
        await interaction.response.send_message("✅ Pytania zostały zaktualizowane!", ephemeral=True)

class SendEmbedModal(discord.ui.Modal, title="📩 Wyślij wiadomość w ramce"):
    channel_id = discord.ui.TextInput(label="ID Kanału", placeholder="Wklej ID kanału...", required=True)
    msg_title = discord.ui.TextInput(label="Tytuł", required=False)
    msg_content = discord.ui.TextInput(label="Treść", style=discord.TextStyle.paragraph, required=True)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            target_channel_id = int(self.channel_id.value.strip())
            channel = interaction.guild.get_channel(target_channel_id)
            if not channel or not isinstance(channel, discord.TextChannel):
                await interaction.response.send_message("❌ Nie znaleziono kanału!", ephemeral=True)
                return
            embed = discord.Embed(title=self.msg_title.value, description=self.msg_content.value, color=discord.Color.blue())
            await channel.send(embed=embed)
            await interaction.response.send_message("✅ Wysłano!", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Błąd: {e}", ephemeral=True)

class RegulaminModal(discord.ui.Modal, title="⚙️ Konfiguracja Regulaminu"):
    content = discord.ui.TextInput(label="Treść regulaminu", style=discord.TextStyle.paragraph, required=True, max_length=4000)

    def __init__(self):
        super().__init__()
        cfg = load_config()
        self.content.default = cfg.get("regulamin_content", "")

    async def on_submit(self, interaction: discord.Interaction):
        cfg = load_config()
        cfg["regulamin_content"] = self.content.value
        save_config(cfg)
        await interaction.response.send_message("✅ Regulamin zaktualizowany!", ephemeral=True)

class UrlopModal(discord.ui.Modal, title="🌴 Zgłoszenie Urlopu"):
    nick = discord.ui.TextInput(label="Nick", required=True, max_length=50)
    termin = discord.ui.TextInput(label="Termin", required=True, max_length=100)
    powod = discord.ui.TextInput(label="Powód", style=discord.TextStyle.paragraph, required=True, max_length=500)

    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(title="🌴 ZGŁOSZENIE URLOPU", description=f"**Od:** {interaction.user.mention}", color=discord.Color.orange())
        embed.add_field(name="Nick", value=self.nick.value, inline=False)
        embed.add_field(name="Termin", value=self.termin.value, inline=False)
        embed.add_field(name="Powód", value=self.powod.value, inline=False)
        await interaction.channel.send(embed=embed)
        await interaction.response.send_message("✅ Urlop zgłoszony!", ephemeral=True)

class PodanieTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Napisz podanie", style=discord.ButtonStyle.success, emoji="📝", custom_id="persistent:fill_podanie_single")
    async def fill_podanie(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PodanieModal())

class KlepaView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.wchodza = []
        self.pozniej = []
        self.nie_moga = []

    @discord.ui.button(label="🟢 Wchodzę (0)", style=discord.ButtonStyle.success, custom_id="klepa_v1:wchodze")
    async def wchodze(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user.mention
        if user in self.pozniej: self.pozniej.remove(user)
        if user in self.nie_moga: self.nie_moga.remove(user)
        if user not in self.wchodza: self.wchodza.append(user)
        await self.update_msg(interaction)

    @discord.ui.button(label="🟡 Będę później (0)", style=discord.ButtonStyle.secondary, custom_id="klepa_v1:pozniej")
    async def pozniej(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user.mention
        if user in self.wchodza: self.wchodza.remove(user)
        if user in self.nie_moga: self.nie_moga.remove(user)
        if user not in self.pozniej: self.pozniej.append(user)
        await self.update_msg(interaction)

    @discord.ui.button(label="🔴 Nie mogę (0)", style=discord.ButtonStyle.danger, custom_id="klepa_v1:niemoge")
    async def niemoge(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user.mention
        if user in self.wchodza: self.wchodza.remove(user)
        if user in self.pozniej: self.pozniej.remove(user)
        if user not in self.nie_moga: self.nie_moga.append(user)
        await self.update_msg(interaction)

    async def update_msg(self, interaction: discord.Interaction):
        self.children[0].label = f"🟢 Wchodzę ({len(self.wchodza)})"
        self.children[1].label = f"🟡 Będę później ({len(self.pozniej)})"
        self.children[2].label = f"🔴 Nie mogę ({len(self.nie_moga)})"
        embed = interaction.message.embeds[0]
        embed.set_field_at(0, name="🟢 Wchodzą:", value=", ".join(self.wchodza) if self.wchodza else "Brak", inline=False)
        embed.set_field_at(1, name="🟡 Będą później:", value=", ".join(self.pozniej) if self.pozniej else "Brak", inline=False)
        embed.set_field_at(2, name="🔴 Nie mogą:", value=", ".join(self.nie_moga) if self.nie_moga else "Brak", inline=False)
        await interaction.response.edit_message(embed=embed, view=self)

class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Otwórz Ticket", style=discord.ButtonStyle.primary, emoji="🎫", custom_id="persistent:open_v40")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        cat = discord.utils.get(guild.categories, name="『ETAP 1』")
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True)
        }
        channel = await guild.create_text_channel(f"🎫-{interaction.user.name}", category=cat, overwrites=overwrites)
        await interaction.response.send_message(f"✅ Ticket stworzony: {channel.mention}", ephemeral=True)
        embed_podanie = discord.Embed(title="📋 FORMULARZ REKRUTACYJNY", description="Kliknij poniższy przycisk.", color=0x3498DB)
        await channel.send(embed=embed_podanie, view=PodanieTicketView())

class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Zacznij Rekrutację", style=discord.ButtonStyle.success, emoji="⚔️", custom_id="persistent:verify_v40")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        role = discord.utils.get(interaction.guild.roles, name="║ do rekru")
        if role:
            await interaction.user.add_roles(role)
        await interaction.response.send_message("✅ Nadano rangę!", ephemeral=True)

# --- ZDARZENIA BOTA ---

@bot.event
async def on_ready():
    global bot_status
    bot_status = f"Zalogowany jako {bot.user}"
    print(f"✅ Bot online: {bot.user}")
    if not keep_alive_ping.is_running():
        keep_alive_ping.start()

@bot.event
async def on_member_join(member: discord.Member):
    channel = member.guild.get_channel(1494791257371705354)
    if channel:
        embed = discord.Embed(
            title="👋 WITAJ NA SERWERZE!",
            description=f"Cześć {member.mention}! Witamy Cię na naszym serwerze gildyjnym.",
            color=discord.Color.green(),
            timestamp=datetime.now()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await channel.send(embed=embed)

@bot.event
async def on_member_remove(member: discord.Member):
    channel = member.guild.get_channel(1494791258931990670)
    if channel:
        embed = discord.Embed(
            title="📤 ŻEGNAJ...",
            description=f"Użytkownik **{member.name}** opuścił nasz serwer.",
            color=discord.Color.red(),
            timestamp=datetime.now()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await channel.send(embed=embed)

# --- KOMENDY SLASH ---

@bot.tree.command(name="setup", description="Buduje pełny setup serwera")
@app_commands.checks.has_permissions(administrator=True)
async def cmd_setup(interaction: discord.Interaction):
    guild = interaction.guild
    ev = guild.default_role
    await interaction.response.defer(ephemeral=True)

    roles_data = {
        "「 」SZEF": 0x992D22,
        "Zarząd": 0x740909,
        "Test Zarząd": 0xE67E22,
        "Rekruter": 0x3498DB,
        "Ticket": 0x00FFFF,
        "「 」Członek": 0x9B59B6,
        "🤝 Sojusz": 0xF1C40F,
        "║ do rekru": 0x2ECC71
    }
    r = {}
    for n, c in roles_data.items():
        role = discord.utils.get(guild.roles, name=n) or await guild.create_role(name=n, color=discord.Color(c), hoist=True)
        r[n] = role

    p_member = {ev: discord.PermissionOverwrite(view_channel=False), r["「 」Członek"]: discord.PermissionOverwrite(view_channel=True), r["🤝 Sojusz"]: discord.PermissionOverwrite(view_channel=True), r["「 」SZEF"]: discord.PermissionOverwrite(view_channel=True)}
    p_rekru = {ev: discord.PermissionOverwrite(view_channel=False), r["Ticket"]: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True), r["Rekruter"]: discord.PermissionOverwrite(view_channel=True), r["Zarząd"]: discord.PermissionOverwrite(view_channel=True), r["「 」SZEF"]: discord.PermissionOverwrite(view_channel=True), r["║ do rekru"]: discord.PermissionOverwrite(view_channel=True)}
    p_logs = {ev: discord.PermissionOverwrite(view_channel=False), r["Zarząd"]: discord.PermissionOverwrite(view_channel=True), r["「 」SZEF"]: discord.PermissionOverwrite(view_channel=True)}

    c_w = await guild.create_category("・ 『Witaj/Żegnamy』 ・")
    await guild.create_text_channel("💻-witamy", category=c_w)
    await guild.create_text_channel("💬-żegnamy", category=c_w)

    c_i = await guild.create_category("・ 『Informacje』 ・", overwrites=p_member)
    await guild.create_text_channel("📢-ogłoszenia", category=c_i)
    ch_reg = await guild.create_text_channel("🚫-regulamin", category=c_i)
    
    cfg = load_config()
    await ch_reg.send(embed=discord.Embed(title=cfg.get("regulamin_title"), description=cfg.get("regulamin_content"), color=discord.Color.gold()))

    c_r = await guild.create_category("・ 『Rekrutacja』 ・", overwrites=p_rekru)
    await guild.create_text_channel("🎫-ticket", category=c_r)
    await guild.create_category("『ETAP 1』", overwrites=p_rekru)
    await guild.create_category("『ETAP 2』", overwrites=p_rekru)
    
    c_a = await guild.create_category("・ 『Administracja』 ・", overwrites={ev: discord.PermissionOverwrite(view_channel=False)})
    await guild.create_text_channel("📑-logi", category=c_a, overwrites=p_logs)
    
    await interaction.followup.send("✅ System zbudowany!", ephemeral=True)

@bot.tree.command(name="weryfikacja", description="Wysyła panel weryfikacji")
@app_commands.checks.has_permissions(administrator=True)
async def cmd_weryfikacja(interaction: discord.Interaction):
    await interaction.channel.send(embed=discord.Embed(title="🛡️ WERYFIKACJA", color=0x2ECC71), view=VerifyView())
    await interaction.response.send_message("✅ Wysłano panel weryfikacji!", ephemeral=True)

@bot.tree.command(name="tickety", description="Wysyła panel ticketów")
@app_commands.checks.has_permissions(administrator=True)
async def cmd_tickety(interaction: discord.Interaction):
    await interaction.channel.send(embed=discord.Embed(title="🎫 REKRUTACJA", color=0x3498DB), view=TicketView())
    await interaction.response.send_message("✅ Wysłano panel ticketów!", ephemeral=True)

@bot.tree.command(name="starttickety", description="Wysyła panel ticketów")
@app_commands.checks.has_permissions(administrator=True)
async def cmd_starttickety(interaction: discord.Interaction):
    await interaction.channel.send(embed=discord.Embed(title="🎫 REKRUTACJA", color=0x3498DB), view=TicketView())
    await interaction.response.send_message("✅ Wysłano panel ticketów!", ephemeral=True)

@bot.tree.command(name="regulamin", description="Wysyła regulamin")
@app_commands.checks.has_permissions(administrator=True)
async def cmd_regulamin(interaction: discord.Interaction):
    cfg = load_config()
    embed = discord.Embed(title=cfg.get("regulamin_title", "📜 REGULAMIN GILDII"), description=cfg.get("regulamin_content"), color=discord.Color.gold(), timestamp=datetime.now())
    await interaction.channel.send(embed=embed)
    await interaction.response.send_message("✅ Wysłano regulamin!", ephemeral=True)

@bot.tree.command(name="ustawregulamin", description="Edytuje regulamin")
@app_commands.checks.has_permissions(administrator=True)
async def cmd_ustawregulamin(interaction: discord.Interaction):
    await interaction.response.send_modal(RegulaminModal())

@bot.tree.command(name="ustawpytania", description="Edytuje pytania podania")
@app_commands.checks.has_permissions(administrator=True)
async def cmd_ustawpytania(interaction: discord.Interaction):
    await interaction.response.send_modal(PytaniaModal())

@bot.tree.command(name="wiadomosc", description="Wysyła wiadomość w ramce")
@app_commands.checks.has_permissions(administrator=True)
async def cmd_wiadomosc(interaction: discord.Interaction):
    await interaction.response.send_modal(SendEmbedModal())

@bot.tree.command(name="clear", description="Czyści wiadomości")
@app_commands.checks.has_permissions(administrator=True)
async def cmd_clear(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=100)
    await interaction.followup.send(f"✅ Wyczyszczono {len(deleted)} wiadomości!", ephemeral=True)

@bot.tree.command(name="urlop", description="Zgłoś urlop")
async def cmd_urlop(interaction: discord.Interaction):
    await interaction.response.send_modal(UrlopModal())

@bot.tree.command(name="acc", description="Akceptuje podanie/kandydata")
async def cmd_acc(interaction: discord.Interaction):
    if not has_management_permission(interaction.user):
        await interaction.response.send_message("❌ Brak uprawnień!", ephemeral=True)
        return

    guild, channel = interaction.guild, interaction.channel
    kandydat = get_ticket_target(channel, interaction.user)
    if not kandydat:
        await interaction.response.send_message("❌ Nie znaleziono kandydata!", ephemeral=True)
        return

    is_etap_2 = "ETAP 2" in (channel.category.name if channel.category else "").upper()
    if is_etap_2:
        await kandydat.add_roles(discord.utils.get(guild.roles, name="「 」Członek"))
        await kandydat.remove_roles(discord.utils.get(guild.roles, name="║ do rekru"))
        await channel.send(embed=discord.Embed(title="🎉 ZALICZONO!", description=f"Gratulacje {kandydat.mention}!", color=discord.Color.gold()))
        await interaction.response.send_message("✅ Ukończono rekrutację.", ephemeral=True)
    else:
        cat_etap2 = discord.utils.get(guild.categories, name="『ETAP 2』")
        if cat_etap2: await channel.edit(category=cat_etap2)
        embed = discord.Embed(
            title="✅ PODANIE ZAAKCEPTOWANE (PRZEJŚCIE DO ETAPU 2)",
            description=(
                f"Gratulacje {kandydat.mention}! Twoje podanie zostało **zaakceptowane** i przechodzisz do **Etapu 2**.\n\n"
                "Jak ktoś będzie miał czas, to Ci odpisze w sprawie dueli. "
                "Tutaj masz kanały na które możesz wbić na rekrutację <#1494791287533076603> lub <#1494791290569621685>\n\n"
                f"**Akceptujący:** {interaction.user.mention}"
            ),
            color=discord.Color.green(),
            timestamp=datetime.now()
        )
        await channel.send(embed=embed)
        await interaction.response.send_message("✅ Przeniesiono do Etapu 2.", ephemeral=True)

@bot.tree.command(name="zamknij", description="Zamyka ticket")
async def cmd_zamknij(interaction: discord.Interaction):
    await interaction.response.send_message("🔒 Zamykanie...", ephemeral=True)
    await interaction.channel.delete()

@bot.tree.command(name="odrz", description="Odrzuca podanie")
async def cmd_odrz(interaction: discord.Interaction):
    await interaction.response.send_message("❌ Odrzucono.", ephemeral=True)
    await interaction.channel.delete()

# --- URUCHAMIANIE ---
async def start_bot_with_retry():
    TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
    if not TOKEN:
        print("❌ Brak tokenu bota w zmiennych środowiskowych (DISCORD_BOT_TOKEN)!")
        return
    async with bot:
        await bot.start(TOKEN)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.run(start_bot_with_retry())
