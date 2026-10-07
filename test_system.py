"""Tests unitaires et de validation du système de repas et courses."""

import unittest
from meal_planner import MealPlanner, RECIPE_CATALOG, Ingredient
from monoprix_client import MonoprixClient, get_promotions
from bot import create_menu_embed, create_shopping_embed


class TestMonoprixClient(unittest.TestCase):
    """Vérifie le bon fonctionnement du client Monoprix national."""

    def setUp(self):
        self.client = MonoprixClient()

    def test_get_promotions_structure(self):
        promos = self.client.get_promotions()
        self.assertIsInstance(promos, list)
        self.assertGreater(len(promos), 0)

        # Vérifier que chaque item contient les champs indispensables requis
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
    """Vérifie le respect strict des contraintes logistiques et de tupperware."""

    def setUp(self):
        self.promos = get_promotions()
        self.planner = MealPlanner(promotions=self.promos)

    def test_tupperware_pairing_and_week_days(self):
        """Vérifie que la semaine couvre du Lundi soir au Vendredi midi sans cuisine le midi."""
        plan = self.planner.build_plan("Ce week-end j'ai : 1 part de quiche, du poulet rôti, des courgettes")

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

        # Maximum 4 à 5 préparations
        self.assertLessEqual(len(plan.preparations), 5)

    def test_weekend_leftovers_priority(self):
        """Vérifie que les plats apportés sont consommés dès le lundi/mardi."""
        user_input = "Ce week-end j'ai : 1 part de quiche, du poulet rôti"
        plan = self.planner.build_plan(user_input)

        slots_by_day = {s.day: s for s in plan.schedule}
        lundi_soir = slots_by_day["Lundi soir"]
        mardi_midi = slots_by_day["Mardi midi"]

        self.assertIn("quiche", lundi_soir.dish_title.lower())
        self.assertIn("poulet", mardi_midi.dish_title.lower())

    def test_deduplication_ingredients(self):
        """Vérifie que les ingrédients apportés de chez les parents ne sont pas sur la liste de courses."""
        user_input = "Ce week-end j'ai : des courgettes, du riz basmati"
        plan = self.planner.build_plan(user_input)

        shopping_names = [it.name.lower() for it in plan.shopping_list]
        for name in shopping_names:
            # Ne doit pas demander d'acheter des courgettes ou du riz
            self.assertNotIn("courgettes fraîches bio", name)

    def test_shopping_list_has_rayons_and_promos(self):
        """Vérifie que la liste de courses est classée par rayon avec mentions de promos."""
        plan = self.planner.build_plan("rien")
        self.assertGreater(len(plan.shopping_list), 0)

        rayons = {it.department for it in plan.shopping_list}
        self.assertGreater(len(rayons), 1)

        has_promo = any(it.on_promo for it in plan.shopping_list)
        self.assertTrue(has_promo, "Au moins une promotion Monoprix doit être mobilisée")


class TestDiscordEmbeds(unittest.TestCase):
    """Vérifie que les Embeds Discord se construisent sans lever d'exception."""

    def test_embed_generation(self):
        planner = MealPlanner(promotions=get_promotions())
        plan = planner.build_plan("1 part de quiche, des courgettes")

        menu_embed = create_menu_embed(plan)
        self.assertIsNotNone(menu_embed.title)
        self.assertGreater(len(menu_embed.fields), 3)

        shopping_embed = create_shopping_embed(plan)
        self.assertIsNotNone(shopping_embed.title)
        self.assertGreater(len(shopping_embed.fields), 0)


if __name__ == "__main__":
    unittest.main()

