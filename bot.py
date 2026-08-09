import asyncio
from datetime import datetime, timedelta
import json
import os
import threading
import time
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from flask import Flask
import google.generativeai as genai

# --- KONFIGURACJA SERWERA WWW (FLASK) ---
app = Flask(__name__)
bot_status = "Uruchamianie..."


@app.route("/")
def home():
    return f"Status bota: {bot_status}"


def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# --- KONFIGURACJA BOTA I PROXY ---
PROXY = os.environ.get("HTTP_PROXY")
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents, proxy=PROXY)

# --- SYSTEM ARCHIWIZACJI TICKETÓW I KONFIGURACJI ---
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
            "→ 3. Głównym celem gildii jest wspólna gra, zabawa i dominacja na serwerze.\n\n"
            "**II. ZASADY KULTURY I KOMUNIKACJI**\n"
            "→ 1. Zakaz nadmiernego toksycznego zachowania wobec członków gildii.\n"
            "→ 2. Zakaz drszczenia mordy (earrape) podczas klep i ważnych akcji.\n"
            "→ 3. Słuchamy liderówki bez zbędnej dyskusji podczas walki.\n"
            "→ 4. Szanujemy się nawzajem – dystans do siebie to podstawa.\n\n"
            "**III. ZASADY ROZGRYWKI**\n"
            "→ 1. Zakaz wynoszenia itemów z baz gildyjnych do sojuszników lub osób trzecich.\n"
            "→ 2. Każdy członek ma obowiązek stawienia się na wezwanie do pomocy (np. obrona bazy).\n"
            "→ 3. Aktywność jest monitorowana – długa nieobecność bez info = wyrzucenie.\n"
            "→ 4. Zakaz używania wspomagaczy (cheatów), które mogą narazić gildię na bany.\n\n"
            "**IV. REKRUTACJA I TICKETY**\n"
            "→ 1. Kłamanie w podaniu skutkuje natychmiastowym odrzuceniem.\n"
            "→ 2. Decyzja rekrutera jest ostateczna.\n"
            "→ 3. Przejście etapu 1 (podanie) nie gwarantuje stałego miejsca w gildii.\n\n"
            "**V. KARY**\n"
            "→ 1. Upomnienie słowne.\n"
            "→ 2. Degradacja lub odebranie uprawnień.\n"
            "→ 3. Ban i wyrzucenie z gildii bez możliwości powrotu."
        ),
        "pytania": [
            "1. Nick z Minecrafta",
            "2. Ile masz lat?",
            "3. Podaj 3 ostatnie gildie",
            "4. Dlaczego my?",
            "5. Czy znasz kogoś?",
        ],
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


# --- FUNKCJE POMOCNICZE DO TRANSKRYPTÓW I UPRAWNIEŃ ---
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
                        transcript_lines.append(
                            f"• **{field.name}** {field.value}"
                        )
                    transcript_lines.append("-" * 30)

        if not message.author.bot and message.content:
            transcript_lines.append(
                f"**{message.author.display_name}**: {message.content}"
            )

    transcript_text = (
        "\n".join(transcript_lines)
        if transcript_lines
        else "Brak treści do wyświetlenia."
    )
    if len(transcript_text) > 4000:
        transcript_text = transcript_text[-4000:]
        transcript_text = (
            "...(historia zbyt długa, wyświetlam końcówkę)...\n"
            + transcript_text
        )
    return transcript_text


async def send_transcript_dm(
    member: discord.Member, channel: discord.TextChannel
):
    transcript_text = await get_transcript_text(channel)
    embed = discord.Embed(
        title=f"📜 Historia rozmowy z ticketa: {channel.name}",
        description=transcript_text,
        color=discord.Color.blue(),
        timestamp=datetime.now(),
    )
    try:
        await member.send(embed=embed)
        return True
    except discord.Forbidden:
        return False


async def send_log(guild, message):
    log_channel = discord.utils.get(guild.text_channels, name="📑-logi")
    if log_channel:
        embed = discord.Embed(
            title="⚙️ SYSTEM LOGS",
            description=message,
            color=discord.Color.dark_grey(),
            timestamp=datetime.now(),
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
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    print(
                        f"⏰ [KEEP-ALIVE] Ping wysłany do {url} | Status: {response.status}"
                    )
        except Exception as e:
            print(f"⚠️ [KEEP-ALIVE] Błąd: {e}")


def get_ticket_target(channel: discord.TextChannel, moderator: discord.Member):
    target = None
    for obj, overwrite in channel.overwrites.items():
        if (
            isinstance(obj, discord.Member)
            and not obj.bot
            and obj != moderator
        ):
            target = obj
            break
    return target


# --- DYNAMICZNE MODALE I FORMULARZE ---


class PodanieModal(discord.ui.Modal, title="📝 Formularz Podania"):

    def __init__(self):
        super().__init__()
        cfg = load_config()
        pytania = cfg.get(
            "pytania",
            [
                "1. Nick z Minecrafta",
                "2. Ile masz lat?",
                "3. Podaj 3 ostatnie gildie",
                "4. Dlaczego my?",
                "5. Czy znasz kogoś?",
            ],
        )

        self.question_inputs = []
        for i, q_text in enumerate(pytania[:5]):
            style = (
                discord.TextStyle.paragraph
                if i in [2, 3]
                else discord.TextStyle.short
            )
            text_input = discord.ui.TextInput(
                label=q_text[:45],
                placeholder="Wpisz odpowiedź...",
                style=style,
                required=True,
                max_length=300,
            )
            self.question_inputs.append(text_input)
            self.add_item(text_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            if self.question_inputs:
                await interaction.user.edit(
                    nick=self.question_inputs[0].value.strip()[:32]
                )
        except Exception:
            pass

        cfg = load_config()
        pytania = cfg.get("pytania", [])

        embed = discord.Embed(
            title=f"📋 PODANIE REKRUTACYJNE — {interaction.user.display_name}",
            description=f"**Kandydat:** {interaction.user.mention}\n**Data:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            color=discord.Color.blue(),
            timestamp=datetime.now(),
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)

        for idx, text_input in enumerate(self.question_inputs):
            q_label = (
                pytania[idx] if idx < len(pytania) else f"Pytanie {idx+1}"
            )
            embed.add_field(
                name=f"➞ {q_label} »", value=text_input.value, inline=False
            )

        await interaction.channel.send(embed=embed)
        await interaction.response.send_message(
            "✅ Podanie zostało wysłane!", ephemeral=True
        )
        await send_log(
            interaction.guild,
            f"📝 **NOWE PODANIE:** Użytkownik {interaction.user.mention} wypełnił podanie.",
        )


class PytaniaModal(
    discord.ui.Modal, title="⚙️ Konfiguracja Pytań Podania"
):
    p1 = discord.ui.TextInput(
        label="Pytanie 1",
        style=discord.TextStyle.short,
        required=True,
        max_length=45,
    )
    p2 = discord.ui.TextInput(
        label="Pytanie 2",
        style=discord.TextStyle.short,
        required=True,
        max_length=45,
    )
    p3 = discord.ui.TextInput(
        label="Pytanie 3",
        style=discord.TextStyle.short,
        required=True,
        max_length=45,
    )
    p4 = discord.ui.TextInput(
        label="Pytanie 4",
        style=discord.TextStyle.short,
        required=True,
        max_length=45,
    )
    p5 = discord.ui.TextInput(
        label="Pytanie 5",
        style=discord.TextStyle.short,
        required=True,
        max_length=45,
    )

    def __init__(self):
        super().__init__()
        cfg = load_config()
        pytania = cfg.get("pytania", ["", "", "", "", ""])
        if len(pytania) > 0:
            self.p1.default = pytania[0]
        if len(pytania) > 1:
            self.p2.default = pytania[1]
        if len(pytania) > 2:
            self.p3.default = pytania[2]
        if len(pytania) > 3:
            self.p4.default = pytania[3]
        if len(pytania) > 4:
            self.p5.default = pytania[4]

    async def on_submit(self, interaction: discord.Interaction):
        cfg = load_config()
        cfg["pytania"] = [
            self.p1.value,
            self.p2.value,
            self.p3.value,
            self.p4.value,
            self.p5.value,
        ]
        save_config(cfg)
        await interaction.response.send_message(
            "✅ Pytania w formularzu podania zostały pomyślnie zaktualizowane!",
            ephemeral=True,
        )


class SendEmbedModal(discord.ui.Modal, title="📩 Wyślij wiadomość w ramce"):
    channel_id = discord.ui.TextInput(
        label="ID Kanału", placeholder="Wklej ID kanału...", required=True
    )
    msg_title = discord.ui.TextInput(
        label="Tytuł wiadomości", placeholder="Nagłówek...", required=False
    )
    msg_content = discord.ui.TextInput(
        label="Treść wiadomości",
        style=discord.TextStyle.paragraph,
        placeholder="Treść...",
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            target_channel_id = int(self.channel_id.value.strip())
            channel = interaction.guild.get_channel(target_channel_id)
            if not channel or not isinstance(channel, discord.TextChannel):
                await interaction.response.send_message(
                    "❌ Nie znaleziono kanału!", ephemeral=True
                )
                return

            embed = discord.Embed(
                title=self.msg_title.value if self.msg_title.value else None,
                description=self.msg_content.value,
                color=discord.Color.blue(),
                timestamp=datetime.now(),
            )
            embed.set_footer(
                text=f"Wysłano przez: {interaction.user.display_name}",
                icon_url=interaction.user.display_avatar.url,
            )
            await channel.send(embed=embed)
            await interaction.response.send_message(
                f"✅ Wiadomość wysłana na {channel.mention}!", ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Błąd: {e}", ephemeral=True
            )


class RegulaminModal(discord.ui.Modal, title="⚙️ Konfiguracja Regulaminu"):
    content = discord.ui.TextInput(
        label="Treść regulaminu",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=4000,
    )

    def __init__(self):
        super().__init__()
        cfg = load_config()
        self.content.default = cfg.get("regulamin_content", "")

    async def on_submit(self, interaction: discord.Interaction):
        cfg = load_config()
        cfg["regulamin_content"] = self.content.value
        save_config(cfg)
        await interaction.response.send_message(
            "✅ Regulamin został zaktualizowany!", ephemeral=True
        )


class UrlopModal(discord.ui.Modal, title="🌴 Zgłoszenie Urlopu"):
    nick = discord.ui.TextInput(
        label="1. Nick",
        placeholder="Twój nick z Minecrafta...",
        required=True,
        max_length=50,
    )
    termin = discord.ui.TextInput(
        label="2. Od kiedy do kiedy",
        placeholder="np. 10.08.2026 - 17.08.2026",
        required=True,
        max_length=100,
    )
    powod = discord.ui.TextInput(
        label="3. Dlaczego Cię nie będzie",
        placeholder="Podaj powód...",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500,
    )

    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🌴 ZGŁOSZENIE URLOPU / NIEOBECNOŚCI",
            description=f"**Kandydat / Członek:** {interaction.user.mention}",
            color=discord.Color.orange(),
            timestamp=datetime.now(),
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.add_field(
            name="➞ 1. Nick »", value=self.nick.value, inline=False
        )
        embed.add_field(
            name="➞ 2. Od kiedy do kiedy »",
            value=self.termin.value,
            inline=False,
        )
        embed.add_field(
            name="➞ 3. Dlaczego Cię nie będzie »",
            value=self.powod.value,
            inline=False,
        )

        await interaction.channel.send(embed=embed)
        await interaction.response.send_message(
            "✅ Twoje zgłoszenie urlopu zostało pomyślnie wysłane!",
            ephemeral=True,
        )
        await send_log(
            interaction.guild,
            f"🌴 **URLOP:** Użytkownik {interaction.user.mention} zgłosił nieobecność ({self.termin.value}).",
        )


# --- WIDOKI I PRZYCISKI ---


class PodanieTicketView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Napisz podanie",
        style=discord.ButtonStyle.success,
        emoji="📝",
        custom_id="persistent:fill_podanie_single",
    )
    async def fill_podanie(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.send_modal(PodanieModal())


class KlepaView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)
        self.wchodza = []
        self.pozniej = []
        self.nie_moga = []

    @discord.ui.button(
        label="🟢 Wchodzę (0)",
        style=discord.ButtonStyle.success,
        custom_id="klepa_v1:wchodze",
    )
    async def wchodze(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        user = interaction.user.mention
        if user in self.pozniej:
            self.pozniej.remove(user)
        if user in self.nie_moga:
            self.nie_moga.remove(user)
        if user not in self.wchodza:
            self.wchodza.append(user)
        await self.update_msg(interaction)

    @discord.ui.button(
        label="🟡 Będę później (0)",
        style=discord.ButtonStyle.secondary,
        custom_id="klepa_v1:pozniej",
    )
    async def pozniej(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        user = interaction.user.mention
        if user in self.wchodza:
            self.wchodza.remove(user)
        if user in self.nie_moga:
            self.nie_moga.remove(user)
        if user not in self.pozniej:
            self.pozniej.append(user)
        await self.update_msg(interaction)

    @discord.ui.button(
        label="🔴 Nie mogę (0)",
        style=discord.ButtonStyle.danger,
        custom_id="klepa_v1:niemoge",
    )
    async def niemoge(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        user = interaction.user.mention
        if user in self.wchodza:
            self.wchodza.remove(user)
        if user in self.pozniej:
            self.pozniej.remove(user)
        if user not in self.nie_moga:
            self.nie_moga.append(user)
        await self.update_msg(interaction)

    async def update_msg(self, interaction: discord.Interaction):
        self.children[0].label = f"🟢 Wchodzę ({len(self.wchodza)})"
        self.children[1].label = f"🟡 Będę później ({len(self.pozniej)})"
        self.children[2].label = f"🔴 Nie mogę ({len(self.nie_moga)})"

        embed = interaction.message.embeds[0]
        embed.set_field_at(
            0,
            name="🟢 Wchodzą:",
            value=", ".join(self.wchodza) if self.wchodza else "Brak",
            inline=False,
        )
        embed.set_field_at(
            1,
            name="🟡 Będą później:",
            value=", ".join(self.pozniej) if self.pozniej else "Brak",
            inline=False,
        )
        embed.set_field_at(
            2,
            name="🔴 Nie mogą:",
            value=", ".join(self.nie_moga) if self.nie_moga else "Brak",
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=self)


class TicketView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Otwórz Ticket",
        style=discord.ButtonStyle.primary,
        emoji="🎫",
        custom_id="persistent:open_v40",
    )
    async def open_ticket(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        guild = interaction.guild
        cat = discord.utils.get(guild.categories, name="『ETAP 1』")

        r_ticket = discord.utils.get(guild.roles, name="Ticket")
        r_zarzad = discord.utils.get(guild.roles, name="Zarząd")
        r_test_zarzad = discord.utils.get(guild.roles, name="Test Zarząd")
        r_szef = discord.utils.get(guild.roles, name="「 」SZEF")
        r_rekruter = discord.utils.get(guild.roles, name="Rekruter")

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=False
            ),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True
            ),
        }

        for role in [r_ticket, r_zarzad, r_test_zarzad, r_szef, r_rekruter]:
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                )

        channel = await guild.create_text_channel(
            f"🎫-{interaction.user.name}", category=cat, overwrites=overwrites
        )
        await interaction.response.send_message(
            f"✅ Ticket stworzony: {channel.mention}", ephemeral=True
        )

        embed_podanie = discord.Embed(
            title="📋 FORMULARZ REKRUTACYJNY",
            description="Kliknij poniższy przycisk **📝 Napisz podanie**, aby otworzyć formularz rekrutacyjny.",
            color=0x3498DB,
        )
        await channel.send(embed=embed_podanie, view=PodanieTicketView())


class VerifyView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Zacznij Rekrutację",
        style=discord.ButtonStyle.success,
        emoji="⚔️",
        custom_id="persistent:verify_v40",
    )
    async def verify(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        role = discord.utils.get(interaction.guild.roles, name="║ do rekru")
        if role:
            await interaction.user.add_roles(role)
        await interaction.response.send_message(
            "✅ Nadano rangę do rekrutacji!", ephemeral=True
        )


class OldTicketsSelect(discord.ui.Select):

    def __init__(self, tickets):
        options = []
        for idx, t in enumerate(tickets[-25:]):
            display_name = t.get("user_name", "Nieznany")
            label = f"{display_name} ({t['closed_at']})"[:100]
            options.append(discord.SelectOption(label=label, value=str(idx)))
        super().__init__(
            placeholder="Wybierz użytkownika...",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.tickets = tickets[-25:]

    async def callback(self, interaction: discord.Interaction):
        selected_idx = int(self.values[0])
        ticket = self.tickets[selected_idx]
        embed = discord.Embed(
            title=f"📜 Archiwalny ticket gracza: {ticket.get('user_name', 'Nieznany')}",
            description=ticket["transcript"],
            color=discord.Color.blue(),
            timestamp=datetime.now(),
        )
        try:
            await interaction.user.send(embed=embed)
            await interaction.response.send_message(
                "✅ Transkrypt wysłany na PV!", ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Masz zablokowane wiadomości prywatne (DM).", ephemeral=True
            )


class OldTicketsView(discord.ui.View):

    def __init__(self, tickets):
        super().__init__(timeout=60)
        self.add_item(OldTicketsSelect(tickets))


# --- BOT EVENTS ---


@bot.event
async def on_ready():
    global bot_status
    bot_status = f"Zalogowany jako {bot.user}"
    print(f"✅ Bot online: {bot.user}")
    if not keep_alive_ping.is_running():
        keep_alive_ping.start()


@bot.event
async def setup_hook():
    bot.add_view(VerifyView())
    bot.add_view(TicketView())
    bot.add_view(KlepaView())
    bot.add_view(PodanieTicketView())
    await bot.tree.sync()
    print("⚡ Zsynchronizowano komendy globalnie!")


@bot.command(name="sync")
@commands.has_permissions(administrator=True)
async def sync_prefix(ctx):
    synced = await bot.tree.sync()
    await ctx.send(f"✅ Zsynchronizowano {len(synced)} komend `/`!")


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
        "║ do rekru": 0x2ECC71,
    }
    r = {}
    for n, c in roles_data.items():
        role = discord.utils.get(guild.roles, name=n) or await guild.create_role(
            name=n, color=discord.Color(c), hoist=True
        )
        r[n] = role

    p_member = {
        ev: discord.PermissionOverwrite(view_channel=False),
        r["「 」Członek"]: discord.PermissionOverwrite(view_channel=True),
        r["🤝 Sojusz"]: discord.PermissionOverwrite(view_channel=True),
        r["「 」SZEF"]: discord.PermissionOverwrite(view_channel=True),
    }
    p_rekru = {
        ev: discord.PermissionOverwrite(view_channel=False),
        r["「 」Członek"]: discord.PermissionOverwrite(view_channel=False),
        r["Ticket"]: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True
        ),
        r["Rekruter"]: discord.PermissionOverwrite(view_channel=True),
        r["Test Zarząd"]: discord.PermissionOverwrite(view_channel=True),
        r["Zarząd"]: discord.PermissionOverwrite(view_channel=True),
        r["「 」SZEF"]: discord.PermissionOverwrite(view_channel=True),
        r["║ do rekru"]: discord.PermissionOverwrite(view_channel=True),
    }
    p_logs = {
        ev: discord.PermissionOverwrite(view_channel=False),
        r["Ticket"]: discord.PermissionOverwrite(view_channel=False),
        r["Test Zarząd"]: discord.PermissionOverwrite(view_channel=False),
        r["Zarząd"]: discord.PermissionOverwrite(view_channel=True),
        r["「 」SZEF"]: discord.PermissionOverwrite(view_channel=True),
    }

    c_w = await guild.create_category("・ 『Witaj/Żegnamy』 ・")
    await guild.create_text_channel("💻-witamy", category=c_w)
    await guild.create_text_channel("💬-żegnamy", category=c_w)

    c_i = await guild.create_category(
        "・ 『Informacje』 ・", overwrites=p_member
    )
    await guild.create_text_channel("📢-ogłoszenia", category=c_i)

    ch_reg = await guild.create_text_channel("🚫-regulamin", category=c_i)
    cfg = load_config()
    embed_reg = discord.Embed(
        title=cfg.get("regulamin_title", "📜 REGULAMIN GILDII"),
        description=cfg.get("regulamin_content"),
        color=discord.Color.gold(),
        timestamp=datetime.now(),
    )
    await ch_reg.send(embed=embed_reg)

    c_v = await guild.create_category("・ 『Weryfikacja』 ・")
    await guild.create_text_channel("🛡️-weryfikacja", category=c_v)
    c_c = await guild.create_category(
        "・ 『Strefa Chatu』 ・", overwrites=p_member
    )
    await guild.create_text_channel("💬-chat", category=c_c)
    await guild.create_text_channel("📷-multimedia", category=c_c)
    c_d = await guild.create_category(
        "・ 『Dane Gildii』 ・", overwrites=p_member
    )
    await guild.create_text_channel("📜-kordy", category=c_d)
    await guild.create_text_channel("📝-formułki", category=c_d)
    c_vo = await guild.create_category(
        "・ 『Kanały Głosowe』 ・", overwrites=p_member
    )
    await guild.create_voice_channel("🔊-Gadanko 1", category=c_vo)
    await guild.create_voice_channel("🔊-Gadanko 2", category=c_vo)
    c_r = await guild.create_category(
        "・ 『Rekrutacja』 ・", overwrites=p_rekru
    )
    await guild.create_text_channel("🎫-ticket", category=c_r)
    await guild.create_voice_channel("🔊-Rekru 1", category=c_r, user_limit=2)
    await guild.create_voice_channel("🔊-Rekru 2", category=c_r, user_limit=2)
    await guild.create_category("『ETAP 1』", overwrites=p_rekru)
    await guild.create_category("『ETAP 2』", overwrites=p_rekru)
    c_a = await guild.create_category(
        "・ 『Administracja』 ・",
        overwrites={ev: discord.PermissionOverwrite(view_channel=False)},
    )
    await guild.create_text_channel("📑-logi", category=c_a, overwrites=p_logs)
    await guild.create_text_channel("⚙-panel", category=c_a, overwrites=p_logs)
    await interaction.followup.send("✅ System zbudowany!", ephemeral=True)


@bot.tree.command(name="weryfikacja", description="Wysyła panel weryfikacji")
@app_commands.checks.has_permissions(administrator=True)
async def cmd_weryfikacja(interaction: discord.Interaction):
    await interaction.channel.send(
        embed=discord.Embed(title="🛡️ WERYFIKACJA", color=0x2ECC71),
        view=VerifyView(),
    )
    await interaction.response.send_message(
        "✅ Wysłano panel weryfikacji!", ephemeral=True
    )


@bot.tree.command(name="tickety", description="Wysyła panel ticketów")
@app_commands.checks.has_permissions(administrator=True)
async def cmd_tickety(interaction: discord.Interaction):
    await interaction.channel.send(
        embed=discord.Embed(title="🎫 REKRUTACJA", color=0x3498DB),
        view=TicketView(),
    )
    await interaction.response.send_message(
        "✅ Wysłano panel ticketów!", ephemeral=True
    )


@bot.tree.command(name="regulamin", description="Wysyła regulamin")
@app_commands.checks.has_permissions(administrator=True)
async def cmd_regulamin(interaction: discord.Interaction):
    cfg = load_config()
    embed = discord.Embed(
        title=cfg.get("regulamin_title", "📜 REGULAMIN GILDII"),
        description=cfg.get("regulamin_content"),
        color=discord.Color.gold(),
        timestamp=datetime.now(),
    )
    await interaction.channel.send(embed=embed)
    await interaction.response.send_message(
        "✅ Wysłano regulamin!", ephemeral=True
    )


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
    await interaction.followup.send(
        f"✅ Wyczyszczono {len(deleted)} wiadomości!", ephemeral=True
    )


@bot.tree.command(name="urlop", description="Zgłoś urlop")
async def cmd_urlop(interaction: discord.Interaction):
    has_czlonek = any(
        role.name == "「 」Członek" for role in interaction.user.roles
    )
    is_admin_or_mgmt = (
        interaction.user.guild_permissions.administrator
        or has_management_permission(interaction.user)
    )
    if not (has_czlonek or is_admin_or_mgmt):
        await interaction.response.send_message(
            "❌ Komenda tylko dla rangi **「 」Członek**!", ephemeral=True
        )
        return
    await interaction.response.send_modal(UrlopModal())


@bot.tree.command(name="klepa", description="Wysyła zapisy na bitwę/klepę")
@app_commands.checks.has_permissions(administrator=True)
async def cmd_klepa(interaction: discord.Interaction, opis: str):
    embed = discord.Embed(
        title="⚔️ WEZWANIE NA KLEPĘ!",
        description=f"**Opis:** {opis}\n\nPotwierdź swoją obecność poniżej!",
        color=discord.Color.red(),
        timestamp=datetime.now(),
    )
    embed.add_field(name="🟢 Wchodzą:", value="Brak", inline=False)
    embed.add_field(name="🟡 Będą później:", value="Brak", inline=False)
    embed.add_field(name="🔴 Nie mogą:", value="Brak", inline=False)
    
    await interaction.channel.send(embed=embed, view=KlepaView())
    await interaction.response.send_message("✅ Wysłano panel zapisów na klepę!", ephemeral=True)


# --- KOMENDY DLA REKRUTERA ---

@bot.tree.command(name="stareticekty", description="Przeglądaj archiwalne tickety rekrutacyjne")
async def cmd_stareticekty(interaction: discord.Interaction):
    if not has_management_permission(interaction.user):
        await interaction.response.send_message("❌ Nie masz uprawnień do tej komendy!", ephemeral=True)
        return
        
    tickets = load_archive()
    if not tickets:
        await interaction.response.send_message("📭 Brak zarchiwizowanych tiketów.", ephemeral=True)
        return
        
    await interaction.response.send_message("📜 Wybierz archiwalny ticket z listy poniżej:", view=OldTicketsView(tickets), ephemeral=True)


@bot.tree.command(name="acc", description="Akceptuje podanie/kandydata i obsługuje etapy")
async def cmd_acc(interaction: discord.Interaction, member: discord.Member, powód: str = "Brak"):
    if not has_management_permission(interaction.user):
        await interaction.response.send_message("❌ Nie masz uprawnień do tej komendy!", ephemeral=True)
        return
        
    guild = interaction.guild
    channel = interaction.channel
    category = channel.category
    cat_name = category.name if category else ""
    
    # Sprawdzamy czy to etap 2 (po nazwie kategorii lub nazwie kanału)
    is_etap_2 = "ETAP 2" in cat_name.upper() or "ETAP 2" in channel.name.upper()
    
    if is_etap_2:
        # Obsługa drugiego etapu: nadaje rangę członka i zabiera rangę "║ do rekru"
        role_czlonek = discord.utils.get(guild.roles, name="「 」Członek")
        role_do_rekru = discord.utils.get(guild.roles, name="║ do rekru")
        
        if role_czlonek:
            await member.add_roles(role_czlonek)
        if role_do_rekru:
            await member.remove_roles(role_do_rekru)
            
        embed = discord.Embed(
            title="🎉 ZALICZONO DRUGI ETAP REKRUTACJI!",
            description=f"Gratulacje {member.mention}! Pomyślnie ukończyłeś rekrutację i otrzymujesz rangę **「 」Członek**!\n**Przez:** {interaction.user.mention}\n**Powód:** {powód}",
            color=discord.Color.gold(),
            timestamp=datetime.now()
        )
        await channel.send(embed=embed)
        await interaction.response.send_message("✅ Pomyślnie ukończono etap 2 rekrutacji dla użytkownika.", ephemeral=True)
        await send_log(guild, f"🎉 **UKOŃCZENIE REKRUTACJI:** Użytkownik {member.mention} awansował na członka gildii przez {interaction.user.mention}.")
    else:
        # Zwykła akceptacja (Etap 1) - przeniesienie wyżej (np. do kategorii Etap 2)
        cat_etap2 = discord.utils.get(guild.categories, name="『ETAP 2』")
        if cat_etap2 and channel.permissions_for(guild.me).manage_channels:
            try:
                await channel.edit(category=cat_etap2)
            except Exception:
                pass
                
        embed = discord.Embed(
            title="✅ PODANIE ZAAKCEPTOWANE (PRZEJŚCIE DO ETAPU 2)",
            description=(
                f"Gratulacje {member.mention}! Twoje podanie zostało **zaakceptowane** i przechodzisz do **Etapu 2**.\n"
                f"**Przez:** {interaction.user.mention}\n"
                f"**Powód:** {powód}\n\n"
                f"Jak ktoś będzie czas, to Ci odpisze w sprawie dueli. "
                f"Tutaj masz kanały na które możesz wbić na rekrutację <#1494791287533076603> lub <#1494791290569621685>"
            ),
            color=discord.Color.green(),
            timestamp=datetime.now()
        )
        await channel.send(embed=embed)
        await interaction.response.send_message("✅ Pomyślnie zaakceptowano podanie i przeniesiono do Etapu 2.", ephemeral=True)
        await send_log(guild, f"✅ **AKCEPTACJA (ETAP 1):** Użytkownik {member.mention} został przesłany do Etapu 2 przez {interaction.user.mention}.")


@bot.tree.command(name="odrz", description="Odrzuca podanie")
async def cmd_odrz(interaction: discord.Interaction, member: discord.Member, powód: str = "Brak podanego powodu"):
    if not has_management_permission(interaction.user):
        await interaction.response.send_message("❌ Nie masz uprawnień do tej komendy!", ephemeral=True)
        return
        
    embed = discord.Embed(
        title="❌ PODANIE ODRZUCONE",
        description=f"Przykro nam {member.mention}, Twoje podanie zostało **odrzucone**.\n**Przez:** {interaction.user.mention}\n**Powód:** {powód}",
        color=discord.Color.red(),
        timestamp=datetime.now()
    )
    await interaction.channel.send(embed=embed)
    await interaction.response.send_message("✅ Pomyślnie odrzucono rekrutację.", ephemeral=True)
    await send_log(interaction.guild, f"❌ **ODRZUCENIE:** Użytkownik {member.mention} został odrzucony przez {interaction.user.mention}.")


@bot.tree.command(name="zamknij", description="Zamyka i archiwizuje bieżący ticket")
async def cmd_zamknij(interaction: discord.Interaction):
    if not has_management_permission(interaction.user):
        await interaction.response.send_message("❌ Nie masz uprawnień do tej komendy!", ephemeral=True)
        return
        
    if not interaction.channel.name.startswith("🎫-"):
        await interaction.response.send_message("❌ Tej komendy można używać tylko w tiketach!", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    channel = interaction.channel
    
    target = get_ticket_target(channel, interaction.user)
    transcript_content = await get_transcript_text(channel)
    
    archive = load_archive()
    archive.append({
        "user_name": target.name if target else "Nieznany",
        "closed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "transcript": transcript_content
    })
    save_archive(archive)
    
    if target:
        success = await send_transcript_dm(target, channel)
        if success:
            await interaction.followup.send("✅ Archiwum zostało wysłane do użytkownika na PV. Zamykam kanał...", ephemeral=True)
        else:
            await interaction.followup.send("⚠️ Nie udało się wysłać archiwum na PV (użytkownik ma zablokowane wiadomości). Zamykam kanał...", ephemeral=True)
    
    await send_log(interaction.guild, f"🔒 **ZAMKNIĘTO TICKET:** Kanał `{channel.name}` został zamknięty przez {interaction.user.mention}.")
    await asyncio.sleep(3)
    await channel.delete()


# --- URUCHOMIENIE APLIKACJI ---

if __name__ == "__main__":
    t = threading.Thread(target=run_flask)
    t.start()
    
    TOKEN = os.environ.get("DISCORD_TOKEN")
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("⚠️ Brak zmiennej środowiskowej DISCORD_TOKEN!")
