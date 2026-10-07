"""Moteur de planification des repas et d'optimisation des courses étudiantes.

Ce module applique les contraintes suivantes :
1. Période : du Lundi soir au Vendredi midi (4 dîners + 4 déjeuners).
2. Règle des tupperwares : chaque dîner cuisiné est doublé pour servir de déjeuner le lendemain.
3. Priorité absolue aux restes et denrées du week-end (consommés dès le lundi/mardi).
4. Sélection de recettes étudiantes économiques alignées sur les promotions Monoprix.
5. Génération d'une liste de courses épurée (uniquement les ingrédients manquants),
   triée par rayon avec mention des promotions.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple


def _normalize_text(text: str) -> str:
    """Normalise une chaîne de texte (minuscules, sans accents) pour la comparaison."""
    text = unicodedata.normalize("NFKD", text).encode("ASCII", "ignore").decode("utf-8")
    return text.lower().strip()


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
    prep_time_min: int = 20
    is_leftover_adaptation: bool = False


# Bibliothèque de recettes étudiantes simples, rapides et économiques
RECIPE_CATALOG: List[Recipe] = [
    Recipe(
        id="poulet_curry_riz",
        title="Poêlée de poulet au curry doux, courgettes & riz basmati",
        ingredients=[
            Ingredient("Filets de poulet", "250g", "Boucherie & Volaille", ["poulet", "volaille", "escalope"]),
            Ingredient("Courgettes fraîches", "2 pièces", "Fruits & Légumes", ["courgette", "courgettes"]),
            Ingredient("Riz basmati", "150g", "Épicerie salée", ["riz", "basmati"]),
            Ingredient("Crème fraîche", "10cl", "Crèmerie & Fromages", ["creme", "creme fraiche"]),
        ],
        instructions_brief="Faire dorer le poulet émincé avec les courgettes en dés, ajouter la crème et une pointe de curry, servir avec le riz.",
        prep_time_min=20,
    ),
    Recipe(
        id="poulet_poivrons_fajitas",
        title="Poêlée mexicaine de poulet aux poivrons tricolores & riz",
        ingredients=[
            Ingredient("Filets de poulet", "250g", "Boucherie & Volaille", ["filet de poulet", "poulet", "escalope"]),
            Ingredient("Poivrons tricolores", "2 pièces", "Fruits & Légumes", ["poivron", "poivrons"]),
            Ingredient("Riz basmati", "150g", "Épicerie salée", ["riz basmati", "riz"]),
            Ingredient("Coulis de tomates", "150g", "Épicerie salée", ["coulis de tomates", "coulis"]),
        ],
        instructions_brief="Saisir les poivrons émincés et le poulet à feu vif, mijoter 10 min avec le coulis, accompagner de riz.",
        prep_time_min=20,
    ),
    Recipe(
        id="saumon_brocolis_riz",
        title="Pavés de saumon rôtis, brocolis vapeur & riz basmati",
        ingredients=[
            Ingredient("Pavés de saumon", "2 pièces", "Poissonnerie", ["saumon", "poisson"]),
            Ingredient("Brocolis frais ou bio", "300g", "Fruits & Légumes", ["brocoli", "brocolis"]),
            Ingredient("Riz basmati", "150g", "Épicerie salée", ["riz", "basmati"]),
            Ingredient("Crème fraîche", "1 cuil. à soupe", "Crèmerie & Fromages", ["creme", "creme fraiche"]),
        ],
        instructions_brief="Cuire les brocolis à la vapeur ou à l'eau, poêler les pavés de saumon 3-4 min par face, servir bien chaud.",
        prep_time_min=15,
    ),
    Recipe(
        id="penne_saumon_creme",
        title="Penne Rigate au saumon fondant et crème d'Isigny",
        ingredients=[
            Ingredient("Pavés de saumon", "2 pièces", "Poissonnerie", ["saumon", "poisson"]),
            Ingredient("Penne Rigate", "200g", "Épicerie salée", ["penne", "pates", "pasta"]),
            Ingredient("Crème fraîche", "15cl", "Crèmerie & Fromages", ["creme", "creme fraiche"]),
        ],
        instructions_brief="Cuire les pâtes al dente, émietter le saumon cuit à la poêle avec la crème et mélanger.",
        prep_time_min=15,
    ),
    Recipe(
        id="dahl_lentilles_corail",
        title="Dahl réconfortant de lentilles corail au lait de coco & riz",
        ingredients=[
            Ingredient("Lentilles corail", "200g", "Épicerie salée", ["lentilles", "lentille"]),
            Ingredient("Lait de coco", "20cl", "Épicerie salée", ["coco", "lait de coco"]),
            Ingredient("Coulis de tomates", "200g", "Épicerie salée", ["coulis", "tomates", "sauce tomate"]),
            Ingredient("Riz basmati", "150g", "Épicerie salée", ["riz", "basmati"]),
        ],
        instructions_brief="Rincer les lentilles, cuire 15 min dans le coulis et le lait de coco avec épices douces, servir sur le riz.",
        prep_time_min=20,
    ),
    Recipe(
        id="chili_express",
        title="Chili express au bœuf haché, tomates Mutti & riz",
        ingredients=[
            Ingredient("Steaks hachés pur bœuf", "2 pièces (200g)", "Boucherie & Volaille", ["boeuf", "steak", "viande hachee"]),
            Ingredient("Coulis de tomates", "250g", "Épicerie salée", ["coulis", "tomates"]),
            Ingredient("Poivrons tricolores", "1 pièce", "Fruits & Légumes", ["poivron", "poivrons"]),
            Ingredient("Riz basmati", "150g", "Épicerie salée", ["riz"]),
        ],
        instructions_brief="Émietter le bœuf dans une poêle chaude, ajouter les dés de poivron et le coulis, laisser réduire.",
        prep_time_min=20,
    ),
    Recipe(
        id="penne_mozzarella_tomates",
        title="Gratin de Penne à la Mozzarella di Bufala et coulis Mutti",
        ingredients=[
            Ingredient("Penne Rigate", "200g", "Épicerie salée", ["penne", "pates"]),
            Ingredient("Mozzarella di Bufala", "1 boule (125g)", "Crèmerie & Fromages", ["mozzarella", "fromage"]),
            Ingredient("Coulis de tomates", "250g", "Épicerie salée", ["coulis", "tomates"]),
            Ingredient("Tomates cerises", "100g", "Fruits & Légumes", ["tomates cerises", "tomate"]),
        ],
        instructions_brief="Mélanger pâtes cuites, coulis et tomates cerises, recouvrir de tranches de mozzarella et gratiner 10 min.",
        prep_time_min=20,
    ),
    Recipe(
        id="poelee_champignons_oeufs",
        title="Poêlée campagnarde de champignons, courgettes & œufs au plat",
        ingredients=[
            Ingredient("Champignons de Paris", "250g", "Fruits & Légumes", ["champignon", "champignons"]),
            Ingredient("Courgettes fraîches", "1 pièce", "Fruits & Légumes", ["courgette", "courgettes"]),
            Ingredient("Œufs plein air", "4 pièces", "Crèmerie & Fromages", ["oeuf", "oeufs"]),
            Ingredient("Penne Rigate", "150g", "Épicerie salée", ["penne", "pates", "riz"]),
        ],
        instructions_brief="Faire sauter les champignons et courgettes à feu vif, cuire les pâtes, accompagner de 2 œufs par personne.",
        prep_time_min=15,
    ),
    Recipe(
        id="poulet_creme_champignons",
        title="Émincé de poulet à la crème d'Isigny et champignons, penne",
        ingredients=[
            Ingredient("Filets de poulet", "250g", "Boucherie & Volaille", ["poulet", "escalope"]),
            Ingredient("Champignons de Paris", "200g", "Fruits & Légumes", ["champignon", "champignons"]),
            Ingredient("Crème fraîche", "15cl", "Crèmerie & Fromages", ["creme", "creme fraiche"]),
            Ingredient("Penne Rigate", "200g", "Épicerie salée", ["penne", "pates"]),
        ],
        instructions_brief="Dorer le poulet et les champignons, déglacer et napper de crème, mélanger aux penne.",
        prep_time_min=20,
    ),
]


@dataclass
class MealSlot:
    day: str
    meal_type: str  # 'Dîner' ou 'Déjeuner'
    dish_title: str
    is_cooked: bool  # True si préparé ce soir-là, False si réchauffage ou tupperware
    tupperware_origin: Optional[str] = None  # Nom du repas dont c'est le tupperware
    notes: str = ""


@dataclass
class PlannedPreparation:
    slot_name: str
    recipe_title: str
    portions: int = 2
    dinner_day: str = ""
    lunch_day: str = ""
    is_weekend_item: bool = False
    source_notes: str = ""


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


class MealPlanner:
    """Orchestrateur de planning hebdomadaire."""

    def __init__(self, promotions: Optional[List[Dict[str, Any]]] = None):
        self.promotions = promotions or []

    def parse_user_inventory(self, raw_text: str) -> Tuple[List[str], List[str]]:
        """Parse le message de l'utilisateur pour extraire les plats cuisinés et les ingrédients bruts.

        Returns:
            Tuple (plats_prepares, ingredients_bruts)
        """
        # Nettoyage des préfixes éventuels de commande Discord
        clean_text = raw_text
        for prefix in ["!planning", "/planning", "planning"]:
            if clean_text.lower().startswith(prefix):
                clean_text = clean_text[len(prefix):].strip()

        # Suppression des introductions courantes
        clean_text = re.sub(
            r"^(ce week[- ]?end j['’]ai\s*:?|j['’]ai\s*:?|ramene\s*:?|voici\s*:?)",
            "",
            clean_text,
            flags=re.IGNORECASE,
        ).strip()

        if not clean_text:
            return [], []

        # Découpage par virgules, retours à la ligne ou tirets
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

        for item in cleaned_items:
            norm = _normalize_text(item)
            if not norm:
                continue
            if any(k in norm for k in keywords_ready):
                ready_dishes.append(item)
            else:
                raw_ingredients.append(item)

        return ready_dishes, raw_ingredients

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
                # Recherche du mot clé délimité ou sous-chaîne significative
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
            # Si l'ingrédient est déjà ramené du week-end : gros bonus (économie totale)
            keywords = [_normalize_text(k) for k in ing.keywords]
            if any(any(kw in item_inv for kw in keywords) for item_inv in norm_inventory):
                score += 15.0
                continue

            # Si l'ingrédient est en promotion chez Monoprix
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

    def build_plan(self, user_text: str) -> WeeklyPlan:
        """Génère le planning complet du Lundi soir au Vendredi midi."""
        ready_dishes, raw_inventory = self.parse_user_inventory(user_text)

        schedule: List[MealSlot] = []
        preparations: List[PlannedPreparation] = []
        weekend_items_used: List[str] = []
        promotions_used: List[Dict[str, Any]] = []
        all_required_ingredients: List[Ingredient] = []

        used_recipe_ids: Set[str] = set()

        # -------------------------------------------------------------
        # ÉTAPE 1 : ÉCOULER LES PLATS ET RESTES DU WEEK-END EN PREMIER
        # -------------------------------------------------------------
        # Les plats maison / restes sont placés dès le Lundi soir et Mardi midi
        ready_pool = list(ready_dishes)

        # Gestion Lundi soir
        if ready_pool:
            first_dish = ready_pool.pop(0)
            weekend_items_used.append(first_dish)
            schedule.append(
                MealSlot(
                    day="Lundi soir",
                    meal_type="Dîner",
                    dish_title=f"Plat maison du week-end : {first_dish}",
                    is_cooked=False,
                    notes="À réchauffer en priorité (fraîcheur maximale)",
                )
            )

            # Mardi midi
            if ready_pool:
                # Un second plat prêt à l'emploi existe (ex: quiche + poulet rôti)
                second_dish = ready_pool.pop(0)
                weekend_items_used.append(second_dish)
                schedule.append(
                    MealSlot(
                        day="Mardi midi",
                        meal_type="Déjeuner",
                        dish_title=f"Reste du week-end : {second_dish}",
                        is_cooked=False,
                        notes="Tupperware maison prêt à emporter / réchauffer",
                    )
                )
            else:
                # Même plat s'il contenait 2 parts ou tupperware direct
                schedule.append(
                    MealSlot(
                        day="Mardi midi",
                        meal_type="Déjeuner",
                        dish_title=f"Tupperware maison : {first_dish} (2e part)",
                        is_cooked=False,
                        tupperware_origin="Lundi soir",
                        notes="Réchauffage simple au micro-ondes (aucune cuisine le midi)",
                    )
                )

            preparations.append(
                PlannedPreparation(
                    slot_name="Restes week-end",
                    recipe_title=first_dish,
                    portions=2,
                    dinner_day="Lundi soir",
                    lunch_day="Mardi midi",
                    is_weekend_item=True,
                    source_notes="Apporté de chez les parents",
                )
            )
            # Les dîners à cuisiner seront donc : Mardi soir, Mercredi soir, Jeudi soir
            cooking_slots = [
                ("Mardi soir", "Mercredi midi"),
                ("Mercredi soir", "Jeudi midi"),
                ("Jeudi soir", "Vendredi midi"),
            ]
        else:
            # Aucun plat tout prêt apporté : il faut cuisiner dès le Lundi soir
            cooking_slots = [
                ("Lundi soir", "Mardi midi"),
                ("Mardi soir", "Mercredi midi"),
                ("Mercredi soir", "Jeudi midi"),
                ("Jeudi soir", "Vendredi midi"),
            ]

        # -------------------------------------------------------------
        # ÉTAPE 2 : SÉLECTIONNER LES RECETTES OPTIMISÉES SELON LES PROMOS
        # -------------------------------------------------------------
        for dinner_day, lunch_day in cooking_slots:
            # Trouver la meilleure recette disponible
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

            # Règle des tupperwares : 1 dîner cuisiné = 2 parts
            schedule.append(
                MealSlot(
                    day=dinner_day,
                    meal_type="Dîner",
                    dish_title=best_recipe.title,
                    is_cooked=True,
                    notes=f"Cuisiner en DOUBLE portion ({best_recipe.instructions_brief})",
                )
            )
            schedule.append(
                MealSlot(
                    day=lunch_day,
                    meal_type="Déjeuner",
                    dish_title=f"Tupperware : {best_recipe.title}",
                    is_cooked=False,
                    tupperware_origin=dinner_day,
                    notes="Reste du dîner de la veille (aucune cuisine le midi)",
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
                    source_notes=f"Temps de prépa : ~{best_recipe.prep_time_min} min",
                )
            )

            all_required_ingredients.extend(best_recipe.ingredients)

        # -------------------------------------------------------------
        # ÉTAPE 3 : DÉDUPLICATION & LISTE DE COURSES OPTIMISÉE
        # -------------------------------------------------------------
        # Ne lister QUE les ingrédients manquants (ceux non apportés par l'utilisateur)
        norm_inventory = [_normalize_text(it) for it in raw_inventory]
        shopping_dict: Dict[str, ShoppingItem] = {}
        total_price = 0.0

        for ing in all_required_ingredients:
            keywords = [_normalize_text(k) for k in ing.keywords] or [_normalize_text(ing.name)]

            # L'utilisateur l'a-t-il déjà apporté ?
            is_brought = False
            for inv_item in norm_inventory:
                if any(kw in inv_item for kw in keywords):
                    is_brought = True
                    if inv_item not in weekend_items_used:
                        weekend_items_used.append(inv_item)
                    break

            if is_brought:
                continue  # Ingrédient déjà disponible gratuitement !

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
                # L'article est déjà dans la liste de courses, on cumule la mention de quantité
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

        # Tri de la liste de courses par rayon pour faciliter les achats
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
        )
