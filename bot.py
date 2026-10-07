"""Bot Discord d'organisation des repas étudiants et des courses Monoprix.

Fonctionnalités :
- Écoute exclusivement dans le salon défini par DISCORD_CHANNEL_ID.
- Analyse les denrées ramenées du week-end (plats maison et ingrédients).
- Récupère les promotions Monoprix de la semaine via monoprix_client.py.
- Construit le planning du Lundi soir au Vendredi midi selon la règle des tupperwares (x2).
- Génère la liste de courses classée par rayon avec mise en valeur des remises.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import List, Optional

import discord
from discord.ext import commands
from dotenv import load_dotenv

from meal_planner import MealPlanner, WeeklyPlan
from monoprix_client import MonoprixClient, get_promotions

# Chargement des variables d'environnement
load_dotenv()

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("repas_bot")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DISCORD_CHANNEL_ID_STR = os.getenv("DISCORD_CHANNEL_ID")

try:
    DISCORD_CHANNEL_ID = int(DISCORD_CHANNEL_ID_STR) if DISCORD_CHANNEL_ID_STR else None
except ValueError:
    logger.error("DISCORD_CHANNEL_ID n'est pas un identifiant numérique valide: %s", DISCORD_CHANNEL_ID_STR)
    DISCORD_CHANNEL_ID = None

# Configuration des intents Discord (Message Content requis pour lire !planning)
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


DAY_COLORS = {
    "lundi": 0x3498DB,      # Bleu
    "mardi": 0x9B59B6,      # Violet
    "mercredi": 0xE67E22,   # Orange
    "jeudi": 0xE74C3C,      # Rouge
}


def create_daily_embeds(plan: WeeklyPlan) -> List[discord.Embed]:
    """Génère une liste d'Embeds distincts, un pour chaque duo (Dîner soir & Déjeuner lendemain)."""
    embeds: List[discord.Embed] = []
    slots_by_day = {slot.day: slot for slot in plan.schedule}

    # Liste ordonnée de tous les créneaux possibles
    days_order = [
        ("Lundi soir", "Mardi midi"),
        ("Mardi soir", "Mercredi midi"),
        ("Mercredi soir", "Jeudi midi"),
        ("Jeudi soir", "Vendredi midi"),
    ]

    for dinner_day, lunch_day in days_order:
        dinner_slot = slots_by_day.get(dinner_day)
        lunch_slot = slots_by_day.get(lunch_day)

        if not dinner_slot or not lunch_slot:
            continue

        day_name = dinner_day.split()[0].lower()
        color = DAY_COLORS.get(day_name, 0x3498DB)

        embed = discord.Embed(
            title=f"🗓️ {dinner_day.split()[0]} soir & {lunch_day.split()[0]} midi",
            color=color,
        )

        # Dîner
        dinner_icon = "🍳" if dinner_slot.is_cooked else "♨️"
        dinner_prep = f"⏱️ ~{dinner_slot.prep_time_min} min" if dinner_slot.prep_time_min else "Prêt à réchauffer"
        dinner_utensils = f" | 🍳 {dinner_slot.utensils}" if dinner_slot.utensils else ""
        dinner_info = (
            f"**{dinner_slot.dish_title}**\n"
            f"*{dinner_prep}{dinner_utensils}*\n"
            f"↳ {dinner_slot.notes}"
        )
        embed.add_field(name=f"{dinner_icon} Dîner ({dinner_day})", value=dinner_info, inline=False)

        # Déjeuner Tupperware
        lunch_info = (
            f"**{lunch_slot.dish_title}**\n"
            f"↳ *{lunch_slot.notes}*"
        )
        embed.add_field(name=f"🥡 Déjeuner ({lunch_day})", value=lunch_info, inline=False)

        embed.set_footer(text="Règle Tupperware x2 : double portion le soir = 0 min de cuisine le midi")
        embeds.append(embed)

    return embeds


def create_menu_embed(plan: WeeklyPlan) -> discord.Embed:
    """Embed de synthèse pour rétro-compatibilité."""
    embed = discord.Embed(
        title="🍱 Planning des Repas",
        description=(
            f"Planning dynamique : **{plan.start_day} ➔ Vendredi midi**\n"
            "🔹 **Règle d'or :** Chaque dîner préparé compte 2 portions pour le tupperware du lendemain.\n"
            "🔹 **Anti-gaspillage :** Priorité absolue aux plats du week-end !"
        ),
        color=0xE60012,
    )
    if plan.weekend_items_used:
        embed.add_field(
            name="🏠 Restes du week-end intégrés",
            value="• " + "\n• ".join(plan.weekend_items_used),
            inline=False,
        )
    return embed


def create_shopping_embed(plan: WeeklyPlan) -> discord.Embed:
    """Crée l'Embed Discord pour la liste de courses Monoprix par rayon."""
    embed = discord.Embed(
        title="🛒 Liste de Courses Optimisée Monoprix",
        description=(
            f"**Période : {plan.start_day} ➔ Vendredi midi** (Offres Nationales Monoprix)\n"
            "Cette liste ne contient **que les ingrédients manquants** pour vos recettes.\n"
            "🌾 *Riz et Pâtes sont déjà dans vos placards (0 € à acheter) !*"
        ),
        color=0x2ECC71,  # Vert économique
    )

    if plan.pantry_staples_used:
        embed.add_field(
            name="🌾 Base Placard (Déjà chez vous)",
            value="• " + "\n• ".join(plan.pantry_staples_used) + "\n*(Non ajoutés au panier Monoprix)*",
            inline=False,
        )

    if not plan.shopping_list:
        embed.add_field(
            name="🎉 Rien à acheter !",
            value="Vos réserves et les denrées rapportées de chez vos parents couvrent tout !",
            inline=False,
        )
        return embed

    # Regroupement des articles par rayon
    by_department: dict[str, list[str]] = {}
    promo_count = 0

    for item in plan.shopping_list:
        dept = item.department or "Autres"
        if dept not in by_department:
            by_department[dept] = []

        promo_badge = ""
        if item.on_promo:
            promo_count += 1
            price_str = f" ({item.base_price:.2f} €)" if item.base_price > 0 else ""
            promo_badge = f" — **[🏷️ {item.promo_details}{price_str}]**"

        qty_str = f" *(besoin : {item.quantity})*" if item.quantity else ""
        by_department[dept].append(f"• {item.name}{qty_str}{promo_badge}")

    for dept, items in by_department.items():
        dept_icon = "📦"
        if "Fruits" in dept or "Légumes" in dept:
            dept_icon = "🥦"
        elif "Boucherie" in dept or "Volaille" in dept:
            dept_icon = "🍗"
        elif "Poisson" in dept:
            dept_icon = "🐟"
        elif "Crèmerie" in dept or "Fromage" in dept:
            dept_icon = "🧀"
        elif "Épicerie" in dept:
            dept_icon = "🍝"

        embed.add_field(
            name=f"{dept_icon} Rayon : {dept}",
            value="\n".join(items),
            inline=False,
        )

    summary_text = (
        f"🏷️ **{promo_count} promotions Monoprix** appliquées sur ce panier.\n"
        f"💰 **Budget estimé :** ~{plan.total_estimated_price:.2f} € (avant remises)\n"
        "💡 *Conseil : Pensez à présenter votre carte de fidélité M' Monoprix en caisse.*"
    )
    embed.add_field(name="📊 Bilan Budget & Économies", value=summary_text, inline=False)

    return embed


async def generate_and_send_plan(
    channel: discord.abc.Messageable,
    user_inventory_text: str,
):
    """Génère le planning dynamique et envoie des messages séparés et aérés."""
    loop = asyncio.get_running_loop()
    client = MonoprixClient()
    promotions = await loop.run_in_executor(None, client.get_promotions)

    planner = MealPlanner(promotions=promotions)
    plan = planner.build_plan(user_inventory_text)

    # 1. Message d'en-tête informatif
    intro_desc = f"Planning optimisé calculé pour : **{plan.start_day} ➔ Vendredi midi**."
    if plan.weekend_items_used:
        intro_desc += "\n🏠 **Plats/ingrédients du week-end intégrés :** " + ", ".join(f"`{w}`" for w in plan.weekend_items_used)

    intro_embed = discord.Embed(
        title="🍱 Votre Organisation des Repas (Express < 15 min)",
        description=intro_desc,
        color=0xE60012,
    )
    await channel.send(embed=intro_embed)

    # 2. Envoi d'un Embed distinct par jour restant (affichage aéré et lisible)
    daily_embeds = create_daily_embeds(plan)
    for daily_embed in daily_embeds:
        await channel.send(embed=daily_embed)

    # 3. Envoi d'un message séparé pour la liste de courses Monoprix par rayon
    shopping_embed = create_shopping_embed(plan)
    await channel.send(embed=shopping_embed)


@bot.event
async def on_ready():
    """Déclenché lorsque le bot est connecté à Discord."""
    logger.info("Bot connecté en tant que %s (ID: %s)", bot.user.name, bot.user.id)
    if DISCORD_CHANNEL_ID:
        logger.info("Écoute restreinte au salon ID: %d", DISCORD_CHANNEL_ID)
    else:
        logger.warning("ATTENTION: DISCORD_CHANNEL_ID n'est pas configuré. Le bot ne traitera aucun message.")

    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="les promos Monoprix 🛒 | !planning",
        )
    )


@bot.event
async def on_message(message: discord.Message):
    """Filtre les messages par salon et traite les demandes d'organisation."""
    # Ignorer les messages émis par des bots (y compris lui-même)
    if message.author.bot:
        return

    # Vérification stricte du canal Discord
    if DISCORD_CHANNEL_ID and message.channel.id != DISCORD_CHANNEL_ID:
        return

    content_strip = message.content.strip()

    # Détection de la commande !planning ou d'un message direct décrivant les restes
    is_planning_cmd = content_strip.startswith("!planning")
    is_freeform_input = content_strip.lower().startswith("ce week-end j'ai") or content_strip.lower().startswith("ce weekend j'ai")

    if is_planning_cmd or is_freeform_input:
        # Extraction du texte des restes
        raw_text = content_strip
        if is_planning_cmd:
            raw_text = content_strip[len("!planning"):].strip()

        if not raw_text:
            help_embed = discord.Embed(
                title="ℹ️ Comment utiliser le bot Repas & Courses",
                description=(
                    "Indiquez ce que vous ramenez de votre week-end pour générer votre planning et votre liste Monoprix !\n\n"
                    "**Exemple d'utilisation :**\n"
                    "`!planning Ce week-end j'ai : 1 part de quiche, du poulet rôti, des courgettes`\n\n"
                    "**Règles appliquées automatiquement :**\n"
                    "• Vos plats maison sont consommés en priorité dès le lundi/mardi.\n"
                    "• Chaque dîner préparé compte 2 portions (dîner + tupperware du lendemain midi).\n"
                    "• La liste de courses sélectionne les meilleures réductions Monoprix de la semaine."
                ),
                color=0x3498DB,
            )
            await message.channel.send(embed=help_embed)
            return

        async with message.channel.typing():
            try:
                await generate_and_send_plan(
                    channel=message.channel,
                    user_inventory_text=raw_text,
                )
            except Exception as exc:
                logger.exception("Erreur lors de la génération du planning: %s", exc)
                await message.channel.send(
                    f"⚠️ Une erreur est survenue lors de la préparation de votre planning : `{exc}`"
                )
        return

    # Traitement des autres commandes enregistrées (ex: !aide, !promos)
    await bot.process_commands(message)


@bot.command(name="aide")
async def cmd_aide(ctx: commands.Context):
    """Affiche le message d'aide."""
    if DISCORD_CHANNEL_ID and ctx.channel.id != DISCORD_CHANNEL_ID:
        return

    embed = discord.Embed(
        title="📖 Guide d'utilisation - Bot Repas & Monoprix",
        description=(
            "Ce bot planifie vos repas étudiants du **Lundi soir au Vendredi midi** et optimise vos courses.\n\n"
            "**Commandes disponibles :**\n"
            "• `!planning <votre inventaire>` : Génère le menu complet et la liste de courses.\n"
            "• `!promos` : Affiche les promotions nationales Monoprix actives de la semaine.\n"
            "• `!aide` : Affiche ce message d'assistance.\n\n"
            "**Exemple :**\n"
            "`!planning Ce week-end j'ai : 1 part de quiche, du poulet rôti, des courgettes, des œufs`"
        ),
        color=0x3498DB,
    )
    await ctx.send(embed=embed)


@bot.command(name="promos")
async def cmd_promos(ctx: commands.Context, rayon: Optional[str] = None):
    """Affiche les promotions nationales en cours chez Monoprix."""
    if DISCORD_CHANNEL_ID and ctx.channel.id != DISCORD_CHANNEL_ID:
        return

    async with ctx.typing():
        loop = asyncio.get_running_loop()
        client = MonoprixClient()
        promotions = await loop.run_in_executor(None, client.get_promotions, rayon)

        if not promotions:
            await ctx.send("Aucune promotion trouvée pour ce rayon actuellement.")
            return

        embed = discord.Embed(
            title="🏷️ Promotions Nationales Monoprix (Catalogue Global)",
            description=f"Top promotions de la semaine ({len(promotions)} articles disponibles) :",
            color=0xE60012,
        )

        for p in promotions[:10]:
            name = p.get("name", "Article")
            price = p.get("base_price", 0.0)
            promo_type = p.get("promo_type", "Promotion")
            dept = p.get("department", "Rayon")
            embed.add_field(
                name=f"{name}",
                value=f"Rayon : *{dept}*\nPrix : **{price:.2f} €** | Remise : **{promo_type}**",
                inline=True,
            )

        await ctx.send(embed=embed)


def main():
    """Point d'entrée principal pour démarrer le bot."""
    if not DISCORD_TOKEN or DISCORD_TOKEN.strip() == "":
        logger.error("Erreur: La variable DISCORD_TOKEN est absente du fichier .env !")
        logger.info("Veuillez renseigner votre token Discord dans .env avant de lancer le bot.")
        sys.exit(1)

    if not DISCORD_CHANNEL_ID:
        logger.warning(
            "Avertissement: DISCORD_CHANNEL_ID n'est pas défini. "
            "Le bot démarrera mais n'écoutera aucun canal tant qu'il n'est pas configuré."
        )

    logger.info("Démarrage du bot avec le catalogue national de promotions Monoprix")
    try:
        bot.run(DISCORD_TOKEN)
    except discord.LoginFailure:
        logger.critical("Échec de connexion : Le token Discord fourni est invalide.")
        sys.exit(1)
    except Exception as exc:
        logger.critical("Erreur fatale lors du démarrage du bot : %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()

