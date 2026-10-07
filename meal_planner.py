"""Moteur de planification des repas et d'optimisation des courses étudiantes.

Ce module applique les contraintes suivantes :
1. Planification dynamique : calcule les repas restants selon le jour actuel (datetime.now())
   ou selon les indications de l'utilisateur (ex. Mercredi soir -> Vendredi midi).
2. Règle des tupperwares : 1 dîner cuisiné le soir en double portion = le déjeuner du lendemain midi.
3. Priorité absolue aux restes et denrées du week-end dès le premier créneau disponible.
4. Stock de base permanent du placard : Riz et Pâtes sont considérés comme toujours en stock
   chez l'étudiant et ne sont JAMAIS ajoutés à la liste de courses.
5. Recettes ultra-simples et rapides : temps de préparation < 15 min, 1 seul ustensile,
   ingrédients économiques et accessibles.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple


def _normalize_text(text: str) -> str:
    """Normalise une chaîne de texte (minuscules, sans accents) pour la comparaison."""
    text = unicodedata.normalize("NFKD", text).encode("ASCII", "ignore").decode("utf-8")
    return text.lower().strip()


# Féculents et condiments toujours présents dans le placard de l'étudiant
PANTRY_STAPLES_KEYWORDS = {
    "riz", "basmati", "pates", "pâtes", "penne", "spaghetti",
    "coquillettes", "tagliatelles", "nouilles", "sel", "poivre", "huile", "eau"
}


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


# Bibliothèque de recettes étudiantes express (< 15 min, 1 seul ustensile, très simple)
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


# Liste ordonnée de tous les créneaux en duo de la semaine étudiante
ALL_WEEK_PAIRS: List[Tuple[str, str]] = [
    ("Lundi soir", "Mardi midi"),      # Index 0
    ("Mardi soir", "Mercredi midi"),   # Index 1
    ("Mercredi soir", "Jeudi midi"),   # Index 2
    ("Jeudi soir", "Vendredi midi"),   # Index 3
]


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


class MealPlanner:
    """Orchestrateur de planning hebdomadaire avec gestion dynamique des jours restants."""

    def __init__(self, promotions: Optional[List[Dict[str, Any]]] = None):
        self.promotions = promotions or []

    def parse_user_inventory(self, raw_text: str) -> Tuple[List[str], List[str]]:
        """Parse le message pour extraire les plats cuisinés et ingrédients bruts."""
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

        # Mots-clés temporels à ignorer dans l'inventaire des aliments
        day_keywords = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "semaine", "partir de"]

        for item in cleaned_items:
            norm = _normalize_text(item)
            if not norm:
                continue
            # Ignorer les fragments purement temporels (ex: "à partir de mercredi", "jusqu'à vendredi")
            if any(dk in norm for dk in day_keywords) and len(norm.split()) <= 4 and not any(k in norm for k in keywords_ready):
                continue
            if any(k in norm for k in keywords_ready):
                ready_dishes.append(item)
            else:
                raw_ingredients.append(item)

        return ready_dishes, raw_ingredients

    def detect_start_day_index(
        self,
        user_text: str,
        current_date: Optional[datetime] = None,
    ) -> int:
        """Détermine l'index du premier créneau (0=Lundi, 1=Mardi, 2=Mercredi, 3=Jeudi).

        Priorité 1 : Mots-clés explicites dans le texte utilisateur ("mercredi", "jeudi", "mardi", "lundi").
        Priorité 2 : Jour actuel via datetime.now().
        """
        norm_text = _normalize_text(user_text)

        # 1. Analyse textuelle explicite
        if "mercredi" in norm_text:
            return 2
        if "jeudi" in norm_text:
            return 3
        if "mardi" in norm_text:
            return 1
        if "lundi" in norm_text or "semaine" in norm_text:
            return 0

        # 2. Détection dynamique selon la date
        now = current_date or datetime.now()
        weekday = now.weekday()  # 0: Lundi, 1: Mardi, 2: Mercredi, 3: Jeudi, 4: Vendredi, 5: Samedi, 6: Dimanche

        if weekday in (0, 1, 2, 3):
            return weekday

        # Du vendredi au dimanche : on planifie par défaut la semaine à venir (Lundi)
        return 0

    def _match_promo_for_ingredient(
        self, ingredient: Ingredient
    ) -> Optional[Dict[str, Any]]:
        """Vérifie si un ingrédient correspond à une promotion Monoprix active."""
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
        """Calcule un score d'adéquation pour une recette selon les promos et l'inventaire."""
        if recipe.id in used_recipes_ids:
            return -100.0

        score = 10.0
        norm_inventory = [_normalize_text(it) for it in raw_inventory]

        for ing in recipe.ingredients:
            # Féculents du placard : neutres, ne pénalisent pas
            if any(s in _normalize_text(ing.name) for s in PANTRY_STAPLES_KEYWORDS):
                continue

            # Ingrédient déjà rapporté de chez les parents : gros bonus
            keywords = [_normalize_text(k) for k in ing.keywords]
            if any(any(kw in item_inv for kw in keywords) for item_inv in norm_inventory):
                score += 15.0
                continue

            # Promotion Monoprix active
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

    def build_plan(
        self,
        user_text: str,
        current_date: Optional[datetime] = None,
    ) -> WeeklyPlan:
        """Génère le planning dynamique selon les jours restants et optimise les courses."""
        ready_dishes, raw_inventory = self.parse_user_inventory(user_text)

        # Détermination dynamique du premier jour
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

        # -------------------------------------------------------------
        # ÉTAPE 1 : ÉCOULER LES PLATS MAISON / RESTES DU WEEK-END EN PREMIER
        # -------------------------------------------------------------
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
                        dish_title=f"Plat maison du week-end : {first_dish}",
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
                            dish_title=f"Reste du week-end : {second_dish}",
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
                        slot_name="Restes week-end",
                        recipe_title=first_dish,
                        portions=2,
                        dinner_day=dinner_day,
                        lunch_day=lunch_day,
                        is_weekend_item=True,
                        source_notes="Apporté de chez les parents",
                        prep_time_min=3,
                        utensils="Micro-ondes",
                    )
                )
            else:
                cooking_slots.append((dinner_day, lunch_day))

        # -------------------------------------------------------------
        # ÉTAPE 2 : SÉLECTIONNER DES RECETTES EXPRESS (< 15 MIN) & PROMOS
        # -------------------------------------------------------------
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

            # Règle tupperware : 1 dîner préparé = 2 parts (dîner + déjeuner du lendemain)
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

        # -------------------------------------------------------------
        # ÉTAPE 3 : COURSES SANS FÉCULENTS (PLACARD) & DÉDUPLICATION
        # -------------------------------------------------------------
        norm_inventory = [_normalize_text(it) for it in raw_inventory]
        shopping_dict: Dict[str, ShoppingItem] = {}
        total_price = 0.0

        for ing in all_required_ingredients:
            ing_norm = _normalize_text(ing.name)
            kws = [_normalize_text(k) for k in ing.keywords] or [ing_norm]

            # RÈGLE DU PLACARD : Riz et Pâtes sont TOUJOURS en réserve chez l'étudiant
            is_pantry_staple = any(
                any(staple in kw for staple in PANTRY_STAPLES_KEYWORDS) for kw in kws
            ) or any(staple in ing_norm for staple in PANTRY_STAPLES_KEYWORDS)

            if is_pantry_staple:
                clean_staple_name = ing.name.replace(" (placard)", "")
                if clean_staple_name not in pantry_staples_used:
                    pantry_staples_used.append(clean_staple_name)
                continue  # Ne JAMAIS ajouter le riz ou les pâtes à la liste de courses !

            # L'utilisateur l'a-t-il apporté de chez ses parents ?
            is_brought = False
            for inv_item in norm_inventory:
                if any(kw in inv_item for kw in kws):
                    is_brought = True
                    if inv_item not in weekend_items_used:
                        weekend_items_used.append(inv_item)
                    break

            if is_brought:
                continue  # Ingrédient déjà possédé gratuitement

            # Matching des promotions Monoprix
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
