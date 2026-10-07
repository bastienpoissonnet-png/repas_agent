"""Tests unitaires et de validation du système de repas et courses."""

import json
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from meal_planner import MealPlanner, RECIPE_CATALOG, Ingredient
from monoprix_client import MonoprixClient, get_promotions
from bot import create_menu_embed, create_daily_embeds, create_shopping_embed


class TestMonoprixClient(unittest.TestCase):
    """Vérifie le bon fonctionnement du client Monoprix national."""

    def setUp(self):
        self.client = MonoprixClient()

    def test_get_promotions_structure(self):
        promos = self.client.get_promotions()
        self.assertIsInstance(promos, list)
        self.assertGreater(len(promos), 0)

        for item in promos:
            self.assertIn("name", item)
            self.assertIn("base_price", item)
            self.assertIn("promo_type", item)
            self.assertIn("department", item)
            self.assertIsInstance(item["base_price"], (int, float))
            self.assertGreater(len(item["name"]), 0)

    def test_category_filter(self):
        promos_legumes = self.client.get_promotions(category="Fruits & Légumes")
        self.assertGreater(len(promos_legumes), 0)
        for p in promos_legumes:
            self.assertTrue(
                "fruits" in p["department"].lower() or "légumes" in p["department"].lower() or "fruits & legumes" in p["department"].lower()
            )


class TestMealPlanner(unittest.TestCase):
    """Vérifie les contraintes logistiques, la dynamique temporelle et les règles tupperware."""

    def setUp(self):
        self.promos = get_promotions()
        self.planner = MealPlanner(promotions=self.promos)

    def test_full_week_tupperware_pairing(self):
        """Vérifie le cycle complet Lundi soir -> Vendredi midi quand démarré le Lundi."""
        dt_lundi = datetime(2026, 10, 5)  # Lundi
        plan = self.planner.build_plan(
            "Ce week-end j'ai : 1 part de quiche, du poulet rôti, des courgettes",
            current_date=dt_lundi,
        )

        # 8 repas au total (4 dîners + 4 déjeuners)
        self.assertEqual(len(plan.schedule), 8)

        slots_by_day = {s.day: s for s in plan.schedule}
        expected_days = [
            "Lundi soir", "Mardi midi",
            "Mardi soir", "Mercredi midi",
            "Mercredi soir", "Jeudi midi",
            "Jeudi soir", "Vendredi midi",
        ]
        for day in expected_days:
            self.assertIn(day, slots_by_day)

        # Règle cruciale : AUCUNE cuisine le midi
        for day in ["Mardi midi", "Mercredi midi", "Jeudi midi", "Vendredi midi"]:
            slot = slots_by_day[day]
            self.assertFalse(slot.is_cooked, f"Le déjeuner {day} ne doit pas nécessiter de cuisine !")

        self.assertLessEqual(len(plan.preparations), 4)

    def test_dynamic_schedule_wednesday_to_friday(self):
        """Vérifie que la planification lancée un mercredi ne génère QUE Mercredi soir -> Vendredi midi."""
        dt_mercredi = datetime(2026, 10, 7)  # Mercredi
        plan = self.planner.build_plan("Rien", current_date=dt_mercredi)

        days_in_schedule = [s.day for s in plan.schedule]
        self.assertEqual(
            days_in_schedule,
            ["Mercredi soir", "Jeudi midi", "Jeudi soir", "Vendredi midi"],
        )
        self.assertEqual(len(plan.preparations), 2)
        slots_by_day = {s.day: s for s in plan.schedule}
        self.assertFalse(slots_by_day["Jeudi midi"].is_cooked)
        self.assertFalse(slots_by_day["Vendredi midi"].is_cooked)

    def test_dynamic_schedule_thursday_to_friday(self):
        """Vérifie que la planification lancée un jeudi ne génère QUE Jeudi soir -> Vendredi midi."""
        dt_jeudi = datetime(2026, 10, 8)  # Jeudi
        plan = self.planner.build_plan("Rien", current_date=dt_jeudi)

        days_in_schedule = [s.day for s in plan.schedule]
        self.assertEqual(days_in_schedule, ["Jeudi soir", "Vendredi midi"])
        self.assertEqual(len(plan.preparations), 1)

    def test_explicit_user_text_day_override(self):
        """Vérifie qu'une mention textuelle (ex. 'mercredi à vendredi') prévaut sur la date courante."""
        dt_lundi = datetime(2026, 10, 5)
        plan = self.planner.build_plan(
            "mercredi à vendredi : 1 part de quiche",
            current_date=dt_lundi,
        )
        days_in_schedule = [s.day for s in plan.schedule]
        self.assertEqual(
            days_in_schedule,
            ["Mercredi soir", "Jeudi midi", "Jeudi soir", "Vendredi midi"],
        )

    def test_pantry_staples_never_in_shopping_list(self):
        """Vérifie que Riz et Pâtes ne sont JAMAIS ajoutés à la liste de courses."""
        dt_lundi = datetime(2026, 10, 5)
        plan = self.planner.build_plan("rien", current_date=dt_lundi)

        shopping_names = [it.name.lower() for it in plan.shopping_list]
        for name in shopping_names:
            self.assertNotIn("riz", name, "Le riz du placard ne doit pas être sur la liste de courses !")
            self.assertNotIn("pates", name, "Les pâtes du placard ne doivent pas être sur la liste !")
            self.assertNotIn("pâtes", name, "Les pâtes du placard ne doivent pas être sur la liste !")
            self.assertNotIn("penne", name, "Les penne du placard ne doivent pas être sur la liste !")

        self.assertGreater(len(plan.pantry_staples_used), 0)

    def test_recipe_prep_time_under_15_min(self):
        """Vérifie que toutes les recettes du catalogue sont rapides (< 15 min de prépa)."""
        for recipe in RECIPE_CATALOG:
            self.assertLess(
                recipe.prep_time_min,
                15,
                f"La recette '{recipe.title}' dépasse 15 min ({recipe.prep_time_min} min)",
            )

    def test_weekend_leftovers_priority(self):
        """Vérifie que les plats apportés sont consommés dès le premier créneau."""
        dt_lundi = datetime(2026, 10, 5)
        user_input = "Ce week-end j'ai : 1 part de quiche, du poulet rôti"
        plan = self.planner.build_plan(user_input, current_date=dt_lundi)

        slots_by_day = {s.day: s for s in plan.schedule}
        lundi_soir = slots_by_day["Lundi soir"]
        mardi_midi = slots_by_day["Mardi midi"]

        self.assertIn("quiche", lundi_soir.dish_title.lower())
        self.assertIn("poulet", mardi_midi.dish_title.lower())

    def test_shopping_list_has_rayons_and_promos(self):
        """Vérifie que la liste de courses est classée par rayon avec mentions de promos."""
        dt_lundi = datetime(2026, 10, 5)
        plan = self.planner.build_plan("rien", current_date=dt_lundi)
        self.assertGreater(len(plan.shopping_list), 0)

        rayons = {it.department for it in plan.shopping_list}
        self.assertGreater(len(rayons), 1)

        has_promo = any(it.on_promo for it in plan.shopping_list)
        self.assertTrue(has_promo, "Au moins une promotion Monoprix doit être mobilisée")


class TestGeminiLLMPlanner(unittest.TestCase):
    """Vérifie l'intégration du LLM Gemini 2.5 Flash avec appel mocké."""

    @patch("meal_planner.genai.Client")
    def test_gemini_planner_execution_mocked(self, mock_client_cls):
        """Vérifie que l'appel à gemini-2.5-flash est correctement construit et parsé."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "start_day": "Mercredi soir",
            "schedule": [
                {
                    "day": "Mercredi soir",
                    "meal_type": "Dîner",
                    "dish_title": "Omelette fondante aux oignons & riz sauté",
                    "is_cooked": True,
                    "notes": "Cuisiner en double portion (1 part ce soir, 1 part demain midi)",
                    "prep_time_min": 10,
                    "utensils": "1 poêle",
                },
                {
                    "day": "Jeudi midi",
                    "meal_type": "Déjeuner",
                    "dish_title": "Tupperware : Omelette fondante aux oignons & riz sauté",
                    "is_cooked": False,
                    "tupperware_origin": "Mercredi soir",
                    "notes": "Tupperware prêt à réchauffer au micro-ondes (0 min cuisine)",
                    "prep_time_min": 0,
                    "utensils": "",
                },
                {
                    "day": "Jeudi soir",
                    "meal_type": "Dîner",
                    "dish_title": "Poêlée express de poulet fermier aux courgettes",
                    "is_cooked": True,
                    "notes": "Cuisiner en double portion",
                    "prep_time_min": 10,
                    "utensils": "1 poêle",
                },
                {
                    "day": "Vendredi midi",
                    "meal_type": "Déjeuner",
                    "dish_title": "Tupperware : Poêlée express de poulet fermier aux courgettes",
                    "is_cooked": False,
                    "tupperware_origin": "Jeudi soir",
                    "notes": "Tupperware prêt à réchauffer",
                    "prep_time_min": 0,
                    "utensils": "",
                },
            ],
            "preparations": [
                {
                    "slot_name": "Préparation 1",
                    "recipe_title": "Omelette fondante aux oignons & riz sauté",
                    "portions": 2,
                    "dinner_day": "Mercredi soir",
                    "lunch_day": "Jeudi midi",
                    "is_weekend_item": False,
                    "source_notes": "Valorisation des 4 oeufs et 3 oignons",
                    "prep_time_min": 10,
                    "utensils": "1 poêle",
                },
                {
                    "slot_name": "Préparation 2",
                    "recipe_title": "Poêlée express de poulet fermier aux courgettes",
                    "portions": 2,
                    "dinner_day": "Jeudi soir",
                    "lunch_day": "Vendredi midi",
                    "is_weekend_item": False,
                    "source_notes": "Promo Monoprix poulet -30%",
                    "prep_time_min": 10,
                    "utensils": "1 poêle",
                },
            ],
            "shopping_list": [
                {
                    "name": "Filets de poulet fermier d'Auvergne (500g)",
                    "department": "Boucherie & Volaille",
                    "quantity": "250g",
                    "on_promo": True,
                    "promo_details": "-30%",
                    "base_price": 7.45,
                },
                {
                    "name": "Courgettes fraîches bio de France (1kg)",
                    "department": "Fruits & Légumes",
                    "quantity": "1 pièce",
                    "on_promo": True,
                    "promo_details": "-20%",
                    "base_price": 2.99,
                },
            ],
            "weekend_items_used": ["4 oeufs", "3 oignons"],
            "pantry_staples_used": ["Riz basmati"],
            "total_estimated_price": 10.44,
        })
        mock_client.models.generate_content.return_value = mock_response

        planner = MealPlanner(promotions=get_promotions(), api_key="fake_test_gemini_key")
        plan = planner.build_plan("il me reste 4 oeufs et 3 oignons")

        # Vérification des assertions métier
        self.assertEqual(len(plan.schedule), 4)
        self.assertEqual(plan.start_day, "Mercredi soir")
        self.assertIn("Omelette", plan.schedule[0].dish_title)
        self.assertFalse(plan.schedule[1].is_cooked, "Le déjeuner du jeudi midi doit être un tupperware")
        self.assertIn("4 oeufs", plan.weekend_items_used)
        self.assertEqual(len(plan.preparations), 2)

        # Vérification qu'aucun féculent (riz/pâtes) n'a été inséré dans la liste de courses
        for item in plan.shopping_list:
            self.assertNotIn("riz", item.name.lower())
            self.assertNotIn("pates", item.name.lower())


class TestDiscordEmbeds(unittest.TestCase):
    """Vérifie que les Embeds découpés par jour et de courses se génèrent proprement."""

    def test_split_daily_embeds_generation(self):
        dt_mercredi = datetime(2026, 10, 7)
        planner = MealPlanner(promotions=get_promotions())
        plan = planner.build_plan("1 part de quiche", current_date=dt_mercredi)

        daily_embeds = create_daily_embeds(plan)
        self.assertEqual(len(daily_embeds), 2)
        for embed in daily_embeds:
            self.assertIsNotNone(embed.title)
            self.assertGreater(len(embed.fields), 1)

        shopping_embed = create_shopping_embed(plan)
        self.assertIsNotNone(shopping_embed.title)
        self.assertGreater(len(shopping_embed.fields), 0)


if __name__ == "__main__":
    unittest.main()
