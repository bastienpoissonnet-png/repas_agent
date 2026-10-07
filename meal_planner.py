"""Moteur de planification des repas et d'optimisation des courses étudiantes via Gemini LLM.

Ce module utilise le modèle gemini-2.5-flash (via le SDK google-genai) pour :
1. Interpréter intelligemment en langage naturel les messages de l'utilisateur
   (restes, ingrédients disparates, envies, contraintes).
2. Déterminer dynamiquement les créneaux restants jusqu'au vendredi midi selon la date/heure.
3. Appliquer la règle d'or du tupperware : 1 dîner préparé en double portion = déjeuner du lendemain.
4. Valoriser en priorité absolue les ingrédients et restes apportés par l'utilisateur.
5. Concevoir des recettes express (< 15 min, 1 seul ustensile) adaptées à un étudiant.
6. Ne JAMAIS ajouter le riz et les pâtes à la liste de courses (considérés toujours en stock dans le placard).
7. Aligner les achats manquants avec les promotions Monoprix de la semaine.

Inclut un mécanisme de secours local robuste si GEMINI_API_KEY n'est pas encore renseignée.
"""

from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Chargement des variables d'environnement
load_dotenv()

logger = logging.getLogger("meal_planner")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

# Import du SDK officiel google-genai
try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False
    logger.warning("Package google-genai non disponible. Le moteur fonctionnera en mode local.")


def _normalize_text(text: str) -> str:
    """Normalise une chaîne de texte (minuscules, sans accents) pour la comparaison."""
    text = unicodedata.normalize("NFKD", text).encode("ASCII", "ignore").decode("utf-8")
    return text.lower().strip()


# Féculents et condiments toujours présents dans le placard de l'étudiant
PANTRY_STAPLES_KEYWORDS = {
    "riz", "basmati", "pates", "pâtes", "penne", "spaghetti",
    "coquillettes", "tagliatelles", "nouilles", "sel", "poivre", "huile", "eau"
}


# =====================================================================
# Modèles Pydantic pour la sortie structurée JSON de Gemini
# =====================================================================

class MealSlotSchema(BaseModel):
    day: str = Field(description="Ex: 'Lundi soir', 'Mardi midi', etc.")
    meal_type: str = Field(description="'Dîner' ou 'Déjeuner'")
    dish_title: str = Field(description="Nom évocateur du plat")
    is_cooked: bool = Field(description="True si préparé le soir même, False si réchauffage/tupperware")
    tupperware_origin: Optional[str] = Field(default=None, description="Jour du dîner d'origine si tupperware")
    notes: str = Field(default="", description="Conseil rapide ou mention tupperware/réchauffage")
    prep_time_min: int = Field(default=10, description="Temps de prépa (< 15 min)")
    utensils: str = Field(default="1 poêle", description="Ustensile principal nécessaire")


class PlannedPreparationSchema(BaseModel):
    slot_name: str = Field(description="Ex: 'Préparation 1' ou 'Restes'")
    recipe_title: str = Field(description="Titre de la recette cuisinée")
    portions: int = Field(default=2, description="Toujours 2 portions pour le tupperware")
    dinner_day: str = Field(description="Jour du dîner")
    lunch_day: str = Field(description="Jour du déjeuner du lendemain")
    is_weekend_item: bool = Field(default=False)
    source_notes: str = Field(default="")
    prep_time_min: int = Field(default=10)
    utensils: str = Field(default="1 poêle")


class ShoppingItemSchema(BaseModel):
    name: str = Field(description="Nom de l'article à acheter chez Monoprix (JAMAIS riz ni pâtes)")
    department: str = Field(description="Rayon Monoprix (ex: 'Fruits & Légumes', 'Boucherie & Volaille', etc.)")
    quantity: str = Field(description="Quantité requise pour les portions prévues")
    on_promo: bool = Field(default=False, description="True si l'article est en promo Monoprix")
    promo_details: str = Field(default="", description="Ex: '-30%', '2e à -50%', '30% cagnotté'")
    base_price: float = Field(default=0.0, description="Prix indicatif en euros")


class GeminiPlanResponseSchema(BaseModel):
    start_day: str = Field(description="Premier créneau actif (ex: 'Mercredi soir')")
    schedule: List[MealSlotSchema]
    preparations: List[PlannedPreparationSchema]
    shopping_list: List[ShoppingItemSchema]
    weekend_items_used: List[str] = Field(default_factory=list, description="Denrées et restes fournis valorisés")
    pantry_staples_used: List[str] = Field(default_factory=list, description="Féculents du placard utilisés (ex: Riz, Pâtes)")
    total_estimated_price: float = Field(default=0.0)


# =====================================================================
# Modèles de données internes (utilisés par bot.py et les tests)
# =====================================================================

@dataclass
class Ingredient:
    name: str
    quantity: str = ""
    department: str = "Épicerie salée"
    keywords: List[str] = field(default_factory=list)


@dataclass
class Recipe:
    id: str
    title: str
    ingredients: List[Ingredient]
    instructions_brief: str
    prep_time_min: int = 10
    utensils: str = "1 poêle"
    is_leftover_adaptation: bool = False


@dataclass
class MealSlot:
    day: str
    meal_type: str  # 'Dîner' ou 'Déjeuner'
    dish_title: str
    is_cooked: bool  # True si préparé ce soir-là, False si réchauffage ou tupperware
    tupperware_origin: Optional[str] = None
    notes: str = ""
    prep_time_min: int = 10
    utensils: str = ""


@dataclass
class PlannedPreparation:
    slot_name: str
    recipe_title: str
    portions: int = 2
    dinner_day: str = ""
    lunch_day: str = ""
    is_weekend_item: bool = False
    source_notes: str = ""
    prep_time_min: int = 10
    utensils: str = ""


@dataclass
class ShoppingItem:
    name: str
    department: str
    quantity: str
    on_promo: bool = False
    promo_details: str = ""
    base_price: float = 0.0


@dataclass
class WeeklyPlan:
    schedule: List[MealSlot]
    preparations: List[PlannedPreparation]
    shopping_list: List[ShoppingItem]
    weekend_items_used: List[str]
    promotions_used: List[Dict[str, Any]]
    total_estimated_price: float = 0.0
    start_day: str = "Lundi soir"
    pantry_staples_used: List[str] = field(default_factory=list)


# Bibliothèque de recettes de secours (fallback local si LLM indisponible)
RECIPE_CATALOG: List[Recipe] = [
    Recipe(
        id="poulet_curry_minute",
        title="Poêlée express de poulet au curry & courgettes (sur riz)",
        ingredients=[
            Ingredient("Filets de poulet", "250g", "Boucherie & Volaille", ["filet de poulet", "poulet", "escalope"]),
            Ingredient("Courgettes fraîches", "1 pièce", "Fruits & Légumes", ["courgette", "courgettes"]),
            Ingredient("Crème fraîche", "10cl", "Crèmerie & Fromages", ["creme", "creme fraiche"]),
            Ingredient("Riz basmati (placard)", "150g", "Épicerie salée", ["riz", "basmati"]),
        ],
        instructions_brief="Dorer le poulet émincé 5 min à la poêle avec les dés de courgette, lier à la crème et au curry. Servir sur le riz.",
        prep_time_min=10,
        utensils="1 poêle",
    ),
    Recipe(
        id="penne_mozzarella_mutti",
        title="Penne minute à la Mozzarella fondante & coulis Mutti",
        ingredients=[
            Ingredient("Coulis de tomates", "250g", "Épicerie salée", ["coulis de tomates", "coulis"]),
            Ingredient("Mozzarella di Bufala", "1 boule (125g)", "Crèmerie & Fromages", ["mozzarella", "fromage"]),
            Ingredient("Tomates cerises", "100g", "Fruits & Légumes", ["tomates cerises"]),
            Ingredient("Penne Rigate (placard)", "200g", "Épicerie salée", ["penne", "pates"]),
        ],
        instructions_brief="Cuire les penne al dente, verser le coulis chaud avec les tomates cerises et la mozzarella coupée qui fond instantanément.",
        prep_time_min=10,
        utensils="1 casserole",
    ),
    Recipe(
        id="saumon_poele_brocolis",
        title="Pavé de saumon poêlé express & brocolis croquants (sur riz)",
        ingredients=[
            Ingredient("Pavés de saumon", "2 pièces", "Poissonnerie", ["saumon", "poisson"]),
            Ingredient("Brocolis frais ou bio", "250g", "Fruits & Légumes", ["brocoli", "brocolis"]),
            Ingredient("Riz basmati (placard)", "150g", "Épicerie salée", ["riz", "basmati"]),
        ],
        instructions_brief="Snacker le saumon 3 min par face à la poêle, cuire les fleurettes de brocolis 5 min dans l'eau bouillante et servir sur le riz.",
        prep_time_min=10,
        utensils="1 poêle",
    ),
    Recipe(
        id="fajita_bowl_poulet",
        title="Fajita-bowl rapide au poulet sauté & poivrons (sur riz)",
        ingredients=[
            Ingredient("Filets de poulet", "250g", "Boucherie & Volaille", ["filet de poulet", "poulet", "escalope"]),
            Ingredient("Poivrons tricolores", "1 pièce", "Fruits & Légumes", ["poivron", "poivrons"]),
            Ingredient("Coulis de tomates", "100g", "Épicerie salée", ["coulis de tomates", "coulis"]),
            Ingredient("Riz basmati (placard)", "150g", "Épicerie salée", ["riz", "basmati"]),
        ],
        instructions_brief="Saisir les lamelles de poulet et de poivron 6 min à feu vif, ajouter 2 cuillères de coulis et déposer sur le riz.",
        prep_time_min=12,
        utensils="1 poêle",
    ),
    Recipe(
        id="hache_italien_penne",
        title="Bœuf haché express façon bolognese & penne",
        ingredients=[
            Ingredient("Steaks hachés pur bœuf", "2 pièces (200g)", "Boucherie & Volaille", ["boeuf", "steak", "viande hachee"]),
            Ingredient("Coulis de tomates", "250g", "Épicerie salée", ["coulis de tomates", "coulis"]),
            Ingredient("Penne Rigate (placard)", "200g", "Épicerie salée", ["penne", "pates"]),
        ],
        instructions_brief="Émietter le steak haché 4 min dans la poêle, verser le coulis Mutti chaud et mélanger aux penne cuites.",
        prep_time_min=10,
        utensils="1 poêle",
    ),
    Recipe(
        id="poelee_champignons_oeufs",
        title="Poêlée minute de champignons dorés & œufs au plat",
        ingredients=[
            Ingredient("Champignons de Paris", "200g", "Fruits & Légumes", ["champignon", "champignons"]),
            Ingredient("Œufs plein air", "4 pièces", "Crèmerie & Fromages", ["oeuf", "oeufs"]),
            Ingredient("Penne Rigate (placard)", "150g", "Épicerie salée", ["penne", "pates"]),
        ],
        instructions_brief="Faire sauter les champignons émincés 5 min dans la poêle, casser 2 œufs par assiette et accompagner de pâtes.",
        prep_time_min=10,
        utensils="1 poêle",
    ),
    Recipe(
        id="saumon_creme_penne",
        title="Penne crémeuses au saumon fondant et ciboulette",
        ingredients=[
            Ingredient("Pavés de saumon", "2 pièces", "Poissonnerie", ["saumon", "poisson"]),
            Ingredient("Crème fraîche", "15cl", "Crèmerie & Fromages", ["creme", "creme fraiche"]),
            Ingredient("Penne Rigate (placard)", "200g", "Épicerie salée", ["penne", "pates"]),
        ],
        instructions_brief="Cuire les penne, émietter le saumon poêlé 4 min directement dedans avec la crème fraîche chaude.",
        prep_time_min=10,
        utensils="1 casserole",
    ),
    Recipe(
        id="dahl_express_lentilles",
        title="Dahl express de lentilles corail au lait de coco (sur riz)",
        ingredients=[
            Ingredient("Lentilles corail", "150g", "Épicerie salée", ["lentilles", "lentille"]),
            Ingredient("Lait de coco", "20cl", "Épicerie salée", ["coco", "lait de coco"]),
            Ingredient("Coulis de tomates", "100g", "Épicerie salée", ["coulis de tomates", "coulis"]),
            Ingredient("Riz basmati (placard)", "150g", "Épicerie salée", ["riz", "basmati"]),
        ],
        instructions_brief="Cuire les lentilles 10 min directement dans le lait de coco et le coulis avec curry/sel, servir sur le riz.",
        prep_time_min=12,
        utensils="1 casserole",
    ),
    Recipe(
        id="omelette_courgettes_fromage",
        title="Omelette moelleuse aux courgettes & mozzarella (sur penne)",
        ingredients=[
            Ingredient("Œufs plein air", "4 pièces", "Crèmerie & Fromages", ["oeuf", "oeufs"]),
            Ingredient("Courgettes fraîches", "1 pièce", "Fruits & Légumes", ["courgette", "courgettes"]),
            Ingredient("Mozzarella di Bufala", "1 boule (125g)", "Crèmerie & Fromages", ["mozzarella", "fromage"]),
            Ingredient("Penne Rigate (placard)", "150g", "Épicerie salée", ["penne", "pates"]),
        ],
        instructions_brief="Râper la courgette à la poêle 3 min, battre les œufs avec les morceaux de mozzarella, cuire 4 min et servir.",
        prep_time_min=10,
        utensils="1 poêle",
    ),
]

ALL_WEEK_PAIRS: List[Tuple[str, str]] = [
    ("Lundi soir", "Mardi midi"),
    ("Mardi soir", "Mercredi midi"),
    ("Mercredi soir", "Jeudi midi"),
    ("Jeudi soir", "Vendredi midi"),
]


class MealPlanner:
    """Orchestrateur intelligent de planning de repas alimenté par Gemini LLM."""

    MODEL_NAME = "gemini-2.5-flash"

    def __init__(
        self,
        promotions: Optional[List[Dict[str, Any]]] = None,
        api_key: Optional[str] = None,
    ):
        """Initialise le planificateur avec les promotions et le client Gemini."""
        self.promotions = promotions or []
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")

        # Vérification si la clé est un placeholder factice ou absente
        if self.api_key in (None, "", "your_gemini_api_key_here"):
            self.api_key = None
            self.client = None
        elif GENAI_AVAILABLE:
            try:
                self.client = genai.Client(api_key=self.api_key)
                logger.info("Client Gemini initialisé avec succès (%s)", self.MODEL_NAME)
            except Exception as err:
                logger.warning("Échec d'initialisation du client Gemini: %s. Utilisation du fallback.", err)
                self.client = None
        else:
            self.client = None

    def _build_system_prompt(self) -> str:
        """Construit le prompt système avec les règles strictes d'organisation étudiante."""
        return (
            "Tu es MealOps, un chef cuisinier expert en logistique et organisation des repas pour un étudiant.\n"
            "Ta mission est de concevoir un planning de repas sur mesure et la liste de courses Monoprix optimale.\n\n"
            "RÈGLES LOGISTIQUES STRICTES :\n"
            "1. PÉRIODE & CRÉNEAUX :\n"
            "   - L'étudiant est présent en appartement du Lundi soir au Vendredi midi.\n"
            "   - Les duos de repas possibles sont :\n"
            "     * Duo 0 : Lundi soir & Mardi midi\n"
            "     * Duo 1 : Mardi soir & Mercredi midi\n"
            "     * Duo 2 : Mercredi soir & Jeudi midi\n"
            "     * Duo 3 : Jeudi soir & Vendredi midi\n"
            "   - Selon la date/heure actuelle (ou l'instruction textuelle), planifie UNIQUEMENT les duos restants jusqu'à Vendredi midi.\n"
            "     * Ex: Si la requête est faite un Mercredi (ou 'mercredi à vendredi') -> planifie uniquement Mercredi soir & Jeudi midi, puis Jeudi soir & Vendredi midi (Duo 2 et Duo 3).\n"
            "     * Ex: Si un Jeudi -> planifie uniquement Jeudi soir & Vendredi midi (Duo 3).\n"
            "     * Ex: Si Lundi ou week-end -> planifie toute la semaine (Duo 0 à 3).\n\n"
            "2. RÈGLE D'OR DU TUPPERWARE (CRUCIALE) :\n"
            "   - L'étudiant ne cuisine JAMAIS le midi.\n"
            "   - Chaque dîner préparé le soir DOIT obligatoirement être cuisiné en DOUBLE PORTION (1 part pour le soir, 1 part en tupperware pour le lendemain midi).\n"
            "   - Les déjeuners sont TOUJOURS des tupperwares du dîner précédent (is_cooked=false) ou des restes prêts à réchauffer.\n\n"
            "3. PRIORITÉ ABSOLUE AUX RESTES ET DENRÉES APPORTÉES :\n"
            "   - Si l'étudiant mentionne des plats cuisinés (ex: quiche, bocal, poulet rôti) ou des ingrédients (ex: '4 oeufs et 3 oignons'), "
            "intègre-les en priorité absolue dès le premier créneau pour éviter le gaspillage.\n"
            "   - Combine intelligemment les ingrédients du message (ex: oeufs + oignons + riz du placard = riz sauté aux oignons & oeufs ou omelette fondante).\n\n"
            "4. RECETTES ULTRA-SIMPLES ET RAPIDES (< 15 MIN) :\n"
            "   - Préparation < 15 min.\n"
            "   - 1 seul ustensile principal (1 poêle ou 1 casserole) pour limiter la vaisselle étudiante.\n"
            "   - Instructions concises et percutantes.\n\n"
            "5. PLACARD & LISTE DE COURSES MONOPRIX :\n"
            "   - RÈGLE DU PLACARD : L'étudiant a TOUJOURS du riz, des pâtes, du sel, du poivre et de l'huile en réserve chez lui.\n"
            "   - NE METS JAMAIS le riz ni les pâtes dans shopping_list ! Indique-les dans pantry_staples_used.\n"
            "   - La liste shopping_list ne contient QUE les ingrédients manquants à acheter chez Monoprix.\n"
            "   - Si un article correspond à une promotion de la liste fournie, associe-le avec on_promo=true et les détails de réduction.\n"
            "   - Classe par rayon (Boucherie & Volaille, Poissonnerie, Fruits & Légumes, Crèmerie & Fromages, Épicerie salée...).\n"
        )

    def _call_gemini_planner(
        self,
        user_text: str,
        current_date: Optional[datetime] = None,
    ) -> WeeklyPlan:
        """Interroge Gemini 2.5 Flash pour générer le planning et la liste en JSON structuré."""
        now = current_date or datetime.now()
        day_names_fr = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
        current_day_str = f"{day_names_fr[now.weekday()]} {now.strftime('%d/%m/%Y %H:%M')}"

        # Résumé des promotions Monoprix disponibles
        promos_summary = [
            f"- {p.get('name')} | Rayon: {p.get('department')} | Promo: {p.get('promo_type')} | Prix base: {p.get('base_price')}€"
            for p in self.promotions[:25]
        ]
        promos_text = "\n".join(promos_summary) if promos_summary else "Aucune promotion spécifique fournie."

        user_prompt = (
            f"=== CONTEXTE ACTUEL ===\n"
            f"Date et heure courante : {current_day_str} (Jour de la semaine index {now.weekday()})\n\n"
            f"=== MESSAGE DE L'ÉTUDIANT ===\n"
            f"\"{user_text}\"\n\n"
            f"=== STOCKS PERMANENTS DU PLACARD (0€ à acheter) ===\n"
            f"Riz basmati, Penne / Pâtes de blé dur, Sel, Poivre, Huile de cuisson.\n\n"
            f"=== PROMOTIONS ACTIVES CHEZ MONOPRIX ===\n"
            f"{promos_text}\n\n"
            f"Mission : Génère le planning complet jusqu'à Vendredi midi et la liste de courses optimisée."
        )

        config = types.GenerateContentConfig(
            system_instruction=self._build_system_prompt(),
            response_mime_type="application/json",
            response_schema=GeminiPlanResponseSchema,
            temperature=0.2,
        )

        logger.info("Envoi de la requête à %s...", self.MODEL_NAME)
        response = self.client.models.generate_content(
            model=self.MODEL_NAME,
            contents=user_prompt,
            config=config,
        )

        raw_json = response.text
        parsed_data = json.loads(raw_json)

        # Conversion du schéma JSON vers les dataclasses internes
        schedule = [
            MealSlot(
                day=s["day"],
                meal_type=s["meal_type"],
                dish_title=s["dish_title"],
                is_cooked=s.get("is_cooked", True),
                tupperware_origin=s.get("tupperware_origin"),
                notes=s.get("notes", ""),
                prep_time_min=s.get("prep_time_min", 10),
                utensils=s.get("utensils", "1 poêle"),
            )
            for s in parsed_data.get("schedule", [])
        ]

        preparations = [
            PlannedPreparation(
                slot_name=p["slot_name"],
                recipe_title=p["recipe_title"],
                portions=p.get("portions", 2),
                dinner_day=p["dinner_day"],
                lunch_day=p["lunch_day"],
                is_weekend_item=p.get("is_weekend_item", False),
                source_notes=p.get("source_notes", ""),
                prep_time_min=p.get("prep_time_min", 10),
                utensils=p.get("utensils", "1 poêle"),
            )
            for p in parsed_data.get("preparations", [])
        ]

        # Filtrage de sécurité strict : ne JAMAIS inclure de riz ou de pâtes dans la liste de courses
        shopping_list = []
        pantry_staples_used = list(parsed_data.get("pantry_staples_used", []))

        for it in parsed_data.get("shopping_list", []):
            item_name = it.get("name", "")
            item_norm = _normalize_text(item_name)
            if any(staple in item_norm for staple in PANTRY_STAPLES_KEYWORDS):
                clean_name = item_name.replace(" (placard)", "")
                if clean_name not in pantry_staples_used:
                    pantry_staples_used.append(clean_name)
                continue

            shopping_list.append(
                ShoppingItem(
                    name=item_name,
                    department=it.get("department", "Épicerie salée"),
                    quantity=it.get("quantity", ""),
                    on_promo=it.get("on_promo", False),
                    promo_details=it.get("promo_details", ""),
                    base_price=float(it.get("base_price", 0.0)),
                )
            )

        # Identifier les promotions Monoprix réellement mobilisées
        promos_used = [
            {"name": item.name, "promo_type": item.promo_details, "department": item.department}
            for item in shopping_list
            if item.on_promo
        ]

        return WeeklyPlan(
            schedule=schedule,
            preparations=preparations,
            shopping_list=sorted(shopping_list, key=lambda x: (x.department, x.name)),
            weekend_items_used=parsed_data.get("weekend_items_used", []),
            promotions_used=promos_used,
            total_estimated_price=round(float(parsed_data.get("total_estimated_price", 0.0)), 2),
            start_day=parsed_data.get("start_day", "Mercredi soir"),
            pantry_staples_used=pantry_staples_used,
        )

    # =====================================================================
    # Moteur de secours local (Fallback déterministe)
    # =====================================================================

    def detect_start_day_index(
        self,
        user_text: str,
        current_date: Optional[datetime] = None,
    ) -> int:
        norm_text = _normalize_text(user_text)
        if "mercredi" in norm_text:
            return 2
        if "jeudi" in norm_text:
            return 3
        if "mardi" in norm_text:
            return 1
        if "lundi" in norm_text or "semaine" in norm_text:
            return 0

        now = current_date or datetime.now()
        weekday = now.weekday()
        if weekday in (0, 1, 2, 3):
            return weekday
        return 0

    def parse_user_inventory(self, raw_text: str) -> Tuple[List[str], List[str]]:
        clean_text = raw_text
        for prefix in ["!planning", "/planning", "planning"]:
            if clean_text.lower().startswith(prefix):
                clean_text = clean_text[len(prefix):].strip()

        clean_text = re.sub(
            r"^(ce week[- ]?end j['’]ai\s*:?|j['’]ai\s*:?|ramene\s*:?|voici\s*:?)",
            "",
            clean_text,
            flags=re.IGNORECASE,
        ).strip()

        if not clean_text:
            return [], []

        raw_items = re.split(r"[,;\n\r\+]|(?:\s+et\s+)", clean_text)
        cleaned_items = [it.strip() for it in raw_items if it.strip()]

        ready_dishes: List[str] = []
        raw_ingredients: List[str] = []

        keywords_ready = [
            "quiche", "tarte", "tourte", "bocal", "blanquette", "lasagne",
            "lasagnes", "gratin", "soupe", "poulet roti", "plat", "reste",
            "restes", "boeuf bourguignon", "tajine", "curry", "part de",
            "parts de", "traiteur", "portion"
        ]
        day_keywords = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "semaine", "partir de"]

        for item in cleaned_items:
            norm = _normalize_text(item)
            if not norm:
                continue
            if any(dk in norm for dk in day_keywords) and len(norm.split()) <= 4 and not any(k in norm for k in keywords_ready):
                continue
            if any(k in norm for k in keywords_ready):
                ready_dishes.append(item)
            else:
                raw_ingredients.append(item)

        return ready_dishes, raw_ingredients

    def _match_promo_for_ingredient(
        self, ingredient: Ingredient
    ) -> Optional[Dict[str, Any]]:
        ing_norm = _normalize_text(ingredient.name)
        keywords = sorted(
            [_normalize_text(k) for k in ingredient.keywords] or [ing_norm],
            key=len,
            reverse=True,
        )
        for kw in keywords:
            for promo in self.promotions:
                p_name = _normalize_text(promo.get("name", ""))
                if kw in p_name:
                    return promo
        return None

    def _score_recipe(
        self,
        recipe: Recipe,
        raw_inventory: List[str],
        used_recipes_ids: Set[str],
    ) -> float:
        if recipe.id in used_recipes_ids:
            return -100.0

        score = 10.0
        norm_inventory = [_normalize_text(it) for it in raw_inventory]

        for ing in recipe.ingredients:
            if any(s in _normalize_text(ing.name) for s in PANTRY_STAPLES_KEYWORDS):
                continue

            keywords = [_normalize_text(k) for k in ing.keywords]
            if any(any(kw in item_inv for kw in keywords) for item_inv in norm_inventory):
                score += 15.0
                continue

            matched_promo = self._match_promo_for_ingredient(ing)
            if matched_promo:
                promo_type = matched_promo.get("promo_type", "")
                if "30" in promo_type:
                    score += 8.0
                elif "50" in promo_type:
                    score += 7.0
                else:
                    score += 5.0

        return score

    def _fallback_rule_based_plan(
        self,
        user_text: str,
        current_date: Optional[datetime] = None,
    ) -> WeeklyPlan:
        """Génère le planning via l'algorithme heuristique de secours si le LLM n'est pas actif."""
        ready_dishes, raw_inventory = self.parse_user_inventory(user_text)
        start_index = self.detect_start_day_index(user_text, current_date=current_date)
        active_pairs = ALL_WEEK_PAIRS[start_index:]

        if not active_pairs:
            active_pairs = [ALL_WEEK_PAIRS[-1]]

        start_day_label = active_pairs[0][0]

        schedule: List[MealSlot] = []
        preparations: List[PlannedPreparation] = []
        weekend_items_used: List[str] = []
        promotions_used: List[Dict[str, Any]] = []
        all_required_ingredients: List[Ingredient] = []
        pantry_staples_used: List[str] = []
        used_recipe_ids: Set[str] = set()

        ready_pool = list(ready_dishes)
        cooking_slots: List[Tuple[str, str]] = []

        for idx, (dinner_day, lunch_day) in enumerate(active_pairs):
            if ready_pool and idx == 0:
                first_dish = ready_pool.pop(0)
                weekend_items_used.append(first_dish)
                schedule.append(
                    MealSlot(
                        day=dinner_day,
                        meal_type="Dîner",
                        dish_title=f"Plat maison : {first_dish}",
                        is_cooked=False,
                        notes="À réchauffer en priorité (fraîcheur maximale)",
                        prep_time_min=3,
                        utensils="Micro-ondes / Casserole",
                    )
                )

                if ready_pool:
                    second_dish = ready_pool.pop(0)
                    weekend_items_used.append(second_dish)
                    schedule.append(
                        MealSlot(
                            day=lunch_day,
                            meal_type="Déjeuner",
                            dish_title=f"Reste : {second_dish}",
                            is_cooked=False,
                            notes="Tupperware maison prêt à emporter (0 min cuisine)",
                            prep_time_min=0,
                        )
                    )
                else:
                    schedule.append(
                        MealSlot(
                            day=lunch_day,
                            meal_type="Déjeuner",
                            dish_title=f"Tupperware maison : {first_dish} (2e part)",
                            is_cooked=False,
                            tupperware_origin=dinner_day,
                            notes="Réchauffage simple au micro-ondes (0 min cuisine)",
                            prep_time_min=0,
                        )
                    )

                preparations.append(
                    PlannedPreparation(
                        slot_name="Restes",
                        recipe_title=first_dish,
                        portions=2,
                        dinner_day=dinner_day,
                        lunch_day=lunch_day,
                        is_weekend_item=True,
                        source_notes="Plat déjà préparé",
                        prep_time_min=3,
                        utensils="Micro-ondes",
                    )
                )
            else:
                cooking_slots.append((dinner_day, lunch_day))

        for dinner_day, lunch_day in cooking_slots:
            best_recipe: Optional[Recipe] = None
            best_score = -999.0

            for recipe in RECIPE_CATALOG:
                score = self._score_recipe(recipe, raw_inventory, used_recipe_ids)
                if score > best_score:
                    best_score = score
                    best_recipe = recipe

            if not best_recipe:
                best_recipe = RECIPE_CATALOG[0]

            used_recipe_ids.add(best_recipe.id)

            schedule.append(
                MealSlot(
                    day=dinner_day,
                    meal_type="Dîner",
                    dish_title=best_recipe.title,
                    is_cooked=True,
                    notes=f"Cuisiner en DOUBLE portion ({best_recipe.instructions_brief})",
                    prep_time_min=best_recipe.prep_time_min,
                    utensils=best_recipe.utensils,
                )
            )
            schedule.append(
                MealSlot(
                    day=lunch_day,
                    meal_type="Déjeuner",
                    dish_title=f"Tupperware : {best_recipe.title}",
                    is_cooked=False,
                    tupperware_origin=dinner_day,
                    notes="Reste de la veille en tupperware — 0 min de cuisine le midi",
                    prep_time_min=0,
                )
            )

            preparations.append(
                PlannedPreparation(
                    slot_name=f"Préparation {len(preparations) + 1}",
                    recipe_title=best_recipe.title,
                    portions=2,
                    dinner_day=dinner_day,
                    lunch_day=lunch_day,
                    is_weekend_item=False,
                    source_notes=f"⏱️ ~{best_recipe.prep_time_min} min | 🍳 {best_recipe.utensils}",
                    prep_time_min=best_recipe.prep_time_min,
                    utensils=best_recipe.utensils,
                )
            )

            all_required_ingredients.extend(best_recipe.ingredients)

        norm_inventory = [_normalize_text(it) for it in raw_inventory]
        shopping_dict: Dict[str, ShoppingItem] = {}
        total_price = 0.0

        for ing in all_required_ingredients:
            ing_norm = _normalize_text(ing.name)
            kws = [_normalize_text(k) for k in ing.keywords] or [ing_norm]

            is_pantry_staple = any(
                any(staple in kw for staple in PANTRY_STAPLES_KEYWORDS) for kw in kws
            ) or any(staple in ing_norm for staple in PANTRY_STAPLES_KEYWORDS)

            if is_pantry_staple:
                clean_staple_name = ing.name.replace(" (placard)", "")
                if clean_staple_name not in pantry_staples_used:
                    pantry_staples_used.append(clean_staple_name)
                continue

            is_brought = False
            for inv_item in norm_inventory:
                if any(kw in inv_item for kw in kws):
                    is_brought = True
                    if inv_item not in weekend_items_used:
                        weekend_items_used.append(inv_item)
                    break

            if is_brought:
                continue

            matched_promo = self._match_promo_for_ingredient(ing)
            if matched_promo:
                item_name = matched_promo.get("name", ing.name)
                dept = matched_promo.get("department", ing.department)
                p_type = matched_promo.get("promo_type", "Promo")
                b_price = float(matched_promo.get("base_price", 2.50))
                is_on_promo = True
            else:
                item_name = ing.name
                dept = ing.department
                p_type = ""
                b_price = 0.0
                is_on_promo = False

            key = _normalize_text(item_name)
            if key in shopping_dict:
                existing = shopping_dict[key]
                if ing.quantity and ing.quantity not in existing.quantity:
                    existing.quantity = f"{existing.quantity} + {ing.quantity}"
                continue

            if matched_promo and matched_promo not in promotions_used:
                promotions_used.append(matched_promo)
                total_price += b_price

            shopping_dict[key] = ShoppingItem(
                name=item_name,
                department=dept,
                quantity=ing.quantity,
                on_promo=is_on_promo,
                promo_details=p_type,
                base_price=b_price,
            )

        sorted_shopping_list = sorted(
            shopping_dict.values(),
            key=lambda item: (item.department, item.name),
        )

        return WeeklyPlan(
            schedule=schedule,
            preparations=preparations,
            shopping_list=sorted_shopping_list,
            weekend_items_used=weekend_items_used,
            promotions_used=promotions_used,
            total_estimated_price=round(total_price, 2),
            start_day=start_day_label,
            pantry_staples_used=pantry_staples_used,
        )

    # =====================================================================
    # Point d'entrée principal : build_plan
    # =====================================================================

    def build_plan(
        self,
        user_text: str,
        current_date: Optional[datetime] = None,
    ) -> WeeklyPlan:
        """Génère le planning des repas via Gemini 2.5 Flash (ou fallback si indisponible)."""
        if self.client:
            try:
                logger.info("Génération du planning via Gemini (%s)...", self.MODEL_NAME)
                return self._call_gemini_planner(user_text, current_date=current_date)
            except Exception as exc:
                logger.warning(
                    "L'appel à Gemini LLM a échoué (%s). Bascule sur le moteur heuristique de secours.",
                    exc,
                )

        logger.info("Génération du planning via le moteur local.")
        return self._fallback_rule_based_plan(user_text, current_date=current_date)
