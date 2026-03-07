# ════════════════════════════════════════════════════════════════════════
# GARDEN ECONOMY COG - Complete Fan Economy System
# ════════════════════════════════════════════════════════════════════════
# 
# Three-Currency System:
# • Petals (everyday currency - daily, tasks, drops)
# • Seeds (engagement currency - tasks, special events)
# • Blooms (ultra-rare prestige currency - forge from petals + seeds)
#
# Features:
# • Daily rewards with streaks
# • Task completion system
# • Shop with purchases & orders
# • Currency drops (scheduled + random)
# • Gifting system
# • Tier/ranking system
# • Transaction history
# • Admin tools
# ════════════════════════════════════════════════════════════════════════

import discord
from discord.ext import commands, tasks
from discord import app_commands
import sqlite3
import json
import os
import random
import asyncio
from datetime import datetime, date, timedelta, timezone

# ═══════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════

DB_PATH = "data/garden_economy.db"

# Channel IDs
SHOP_CHANNEL_ID = 0  # Where shop commands work
DROPS_CHANNEL_ID = 0  # Where currency drops appear
ORDERS_CHANNEL_ID = 0  # Where purchase orders go for fulfillment

# Role IDs
VIP_ROLE_ID = 0  # Double daily rewards
TIER_ROLES = {
    1: 0,  # Sprout tier
    2: 0,  # Seedling tier
    3: 0,  # Bud tier
    4: 0,  # Bloom tier
    5: 0,  # Garden Master tier
}

# Currency Emojis (change these to your custom emoji IDs)
PETAL_EMOJI = "🌸"  # or <:petal:123456789>
SEED_EMOJI = "🌱"   # or <:seed:123456789>
BLOOM_EMOJI = "🌺"  # or <:bloom:123456789>

# Economy Settings
DAILY_PETALS = 25
DAILY_SEEDS = 5
VIP_DAILY_MULTIPLIER = 2

BLOOM_FORGE_COST_PETALS = 100
BLOOM_FORGE_COST_SEEDS = 50

# Drop Settings
RANDOM_DROP_INTERVAL_MIN = 2  # hours
RANDOM_DROP_INTERVAL_MAX = 6  # hours
RANDOM_DROP_AMOUNT_MIN = 15
RANDOM_DROP_AMOUNT_MAX = 50

# Tier Thresholds (total earned)
TIER_THRESHOLDS = {
    1: 0,
    2: 500,
    3: 2000,
    4: 5000,
    5: 10000,
}

# Admin User IDs
ADMIN_IDS = {448896936481652777, 754324103816806500}

# ═══════════════════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════════════════

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Initialize database with all tables"""
    os.makedirs(os.path.dirname(DB_PATH) if os.path.dirname(DB_PATH) else ".", exist_ok=True)
    
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            discord_id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            petals INTEGER NOT NULL DEFAULT 0,
            seeds INTEGER NOT NULL DEFAULT 0,
            blooms INTEGER NOT NULL DEFAULT 0,
            total_earned INTEGER NOT NULL DEFAULT 0,
            total_spent INTEGER NOT NULL DEFAULT 0,
            last_daily TEXT,
            streak_days INTEGER NOT NULL DEFAULT 0,
            tier INTEGER NOT NULL DEFAULT 1,
            joined_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            price INTEGER NOT NULL,
            price_currency TEXT NOT NULL DEFAULT 'petals',
            quantity INTEGER NOT NULL,
            remaining INTEGER NOT NULL,
            category TEXT NOT NULL,
            emoji TEXT NOT NULL DEFAULT '🌸',
            active INTEGER NOT NULL DEFAULT 1
        );
        
        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id TEXT NOT NULL REFERENCES users(discord_id),
            item_id INTEGER NOT NULL REFERENCES items(id),
            item_name TEXT NOT NULL,
            price INTEGER NOT NULL,
            price_currency TEXT NOT NULL DEFAULT 'petals',
            bought_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_id INTEGER NOT NULL REFERENCES purchases(id),
            discord_id TEXT NOT NULL REFERENCES users(discord_id),
            item_name TEXT NOT NULL,
            price INTEGER NOT NULL,
            price_currency TEXT NOT NULL DEFAULT 'petals',
            status TEXT NOT NULL DEFAULT 'pending',
            note TEXT,
            message_id TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id TEXT NOT NULL REFERENCES users(discord_id),
            currency TEXT NOT NULL DEFAULT 'petals',
            amount INTEGER NOT NULL,
            reason TEXT NOT NULL,
            tx_type TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            reward_petals INTEGER NOT NULL DEFAULT 0,
            reward_seeds INTEGER NOT NULL DEFAULT 0,
            emoji TEXT NOT NULL DEFAULT '✨',
            repeatable INTEGER NOT NULL DEFAULT 0
        );
        
        CREATE TABLE IF NOT EXISTS completed_tasks (
            discord_id TEXT NOT NULL REFERENCES users(discord_id),
            task_id TEXT NOT NULL,
            completed_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (discord_id, task_id)
        );
        
        CREATE TABLE IF NOT EXISTS drops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            currency TEXT NOT NULL DEFAULT 'petals',
            amount INTEGER NOT NULL,
            drop_at TEXT NOT NULL,
            message_id TEXT,
            status TEXT NOT NULL DEFAULT 'scheduled',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """)
        
        # Seed default tasks
        default_tasks = [
            ("intro", "Introduce yourself", "Post in #introductions", 50, 10, "👋", 0),
            ("first_purchase", "Make your first purchase", "Buy anything from the shop", 25, 5, "🛒", 0),
            ("streak_7", "7-day daily streak", "Claim daily for 7 days straight", 100, 25, "🔥", 0),
            ("forge_bloom", "Forge your first Bloom", "Use /forge to create a Bloom", 0, 0, "🌺", 0),
        ]
        
        for task in default_tasks:
            conn.execute("""
                INSERT OR IGNORE INTO tasks 
                (id, name, description, reward_petals, reward_seeds, emoji, repeatable)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, task)
        
        conn.commit()


# ═══════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════

def get_or_create_user(discord_id: str, username: str):
    """Get user or create if doesn't exist"""
    with get_conn() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE discord_id = ?", (discord_id,)
        ).fetchone()
        
        if not user:
            conn.execute(
                "INSERT INTO users (discord_id, username) VALUES (?, ?)",
                (discord_id, username)
            )
            conn.commit()
            user = conn.execute(
                "SELECT * FROM users WHERE discord_id = ?", (discord_id,)
            ).fetchone()
        
        return dict(user)


def update_currency(discord_id: str, currency: str, amount: int, reason: str, tx_type: str):
    """Update user currency and log transaction"""
    with get_conn() as conn:
        if amount > 0:
            conn.execute(
                f"UPDATE users SET {currency} = {currency} + ?, total_earned = total_earned + ? WHERE discord_id = ?",
                (amount, amount, discord_id)
            )
        else:
            conn.execute(
                f"UPDATE users SET {currency} = {currency} + ?, total_spent = total_spent + ? WHERE discord_id = ?",
                (amount, abs(amount), discord_id)
            )
        
        conn.execute(
            "INSERT INTO transactions (discord_id, currency, amount, reason, tx_type) VALUES (?, ?, ?, ?, ?)",
            (discord_id, currency, amount, reason, tx_type)
        )
        conn.commit()


def check_and_update_tier(discord_id: str):
    """Check if user should tier up"""
    with get_conn() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE discord_id = ?", (discord_id,)
        ).fetchone()
        
        if not user:
            return None
        
        current_tier = user["tier"]
        total_earned = user["total_earned"]
        
        new_tier = current_tier
        for tier, threshold in sorted(TIER_THRESHOLDS.items(), reverse=True):
            if total_earned >= threshold:
                new_tier = tier
                break
        
        if new_tier > current_tier:
            conn.execute(
                "UPDATE users SET tier = ? WHERE discord_id = ?",
                (new_tier, discord_id)
            )
            conn.commit()
            return new_tier
        
        return None


# ═══════════════════════════════════════════════════════════════════════
# GARDEN ECONOMY COG
# ═══════════════════════════════════════════════════════════════════════

class GardenEconomyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        init_db()
        self.random_drops.start()
    
    def cog_unload(self):
        self.random_drops.cancel()
    
    # ═══════════════════════════════════════════════════════════════════
    # RANDOM CURRENCY DROPS
    # ═══════════════════════════════════════════════════════════════════
    
    @tasks.loop(hours=1)
    async def random_drops(self):
        """Random currency drops every few hours"""
        # Random chance
        if random.random() > 0.4:  # 40% chance per hour
            return
        
        channel = self.bot.get_channel(DROPS_CHANNEL_ID)
        if not channel:
            return
        
        # Random amount and currency
        currency = random.choice(["petals", "petals", "seeds"])  # Petals more common
        amount = random.randint(RANDOM_DROP_AMOUNT_MIN, RANDOM_DROP_AMOUNT_MAX)
        
        emoji = PETAL_EMOJI if currency == "petals" else SEED_EMOJI
        
        embed = discord.Embed(
            title=f"{emoji} Wild {currency.title()} Appeared!",
            description=f"**{amount} {currency}** just dropped!\n\nFirst to claim gets them! 🌱",
            color=0xC5DEB8
        )
        embed.set_footer(text="Type .claim to grab them!")
        
        await channel.send(embed=embed)
        
        # TODO: Add claim button/command
    
    @random_drops.before_loop
    async def before_random_drops(self):
        await self.bot.wait_until_ready()
        # Random initial delay
        await asyncio.sleep(random.randint(0, 3600))
    
    # ═══════════════════════════════════════════════════════════════════
    # DAILY COMMAND
    # ═══════════════════════════════════════════════════════════════════
    
    @commands.command(name="gdaily", aliases=["gd"])
    async def daily(self, ctx):
        """Claim your daily petals and seeds"""
        user = get_or_create_user(str(ctx.author.id), ctx.author.display_name)
        
        today = date.today().isoformat()
        
        if user["last_daily"] == today:
            await ctx.send(f"🌸 You've already claimed your daily reward today! Come back tomorrow.")
            return
        
        # Check VIP
        is_vip = VIP_ROLE_ID and any(r.id == VIP_ROLE_ID for r in ctx.author.roles)
        multiplier = VIP_DAILY_MULTIPLIER if is_vip else 1
        
        petals = DAILY_PETALS * multiplier
        seeds = DAILY_SEEDS * multiplier
        
        # Update streak
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        if user["last_daily"] == yesterday:
            streak = user["streak_days"] + 1
        else:
            streak = 1
        
        # Add currency
        update_currency(str(ctx.author.id), "petals", petals, "Daily claim", "daily")
        update_currency(str(ctx.author.id), "seeds", seeds, "Daily claim", "daily")
        
        # Update last daily and streak
        with get_conn() as conn:
            conn.execute(
                "UPDATE users SET last_daily = ?, streak_days = ? WHERE discord_id = ?",
                (today, streak, str(ctx.author.id))
            )
            conn.commit()
        
        # Check tier up
        new_tier = check_and_update_tier(str(ctx.author.id))
        
        embed = discord.Embed(
            title="🌱 Daily Reward Claimed!",
            description=f"**+{petals}** {PETAL_EMOJI} Petals\n**+{seeds}** {SEED_EMOJI} Seeds",
            color=0x2ecc71
        )
        embed.add_field(name="Streak", value=f"🔥 {streak} days", inline=True)
        
        if is_vip:
            embed.add_field(name="VIP Bonus", value=f"✨ {multiplier}x multiplier!", inline=True)
        
        await ctx.send(embed=embed)
        
        if new_tier:
            await ctx.send(f"🎉 **Tier Up!** You've reached **Tier {new_tier}**!")
    
    # ═══════════════════════════════════════════════════════════════════
    # BALANCE COMMAND
    # ═══════════════════════════════════════════════════════════════════
    
    @commands.command(name="gbalance", aliases=["gbal", "gwallet"])
    async def balance(self, ctx, member: discord.Member = None):
        """Check your currency balance"""
        target = member or ctx.author
        user = get_or_create_user(str(target.id), target.display_name)
        
        embed = discord.Embed(
            title=f"🌸 {target.display_name}'s Garden Wallet",
            color=0xC5DEB8
        )
        
        embed.add_field(
            name=f"{PETAL_EMOJI} Petals",
            value=f"**{user['petals']}**",
            inline=True
        )
        embed.add_field(
            name=f"{SEED_EMOJI} Seeds",
            value=f"**{user['seeds']}**",
            inline=True
        )
        embed.add_field(
            name=f"{BLOOM_EMOJI} Blooms",
            value=f"**{user['blooms']}**",
            inline=True
        )
        
        embed.add_field(name="Tier", value=f"⭐ Tier {user['tier']}", inline=True)
        embed.add_field(name="Streak", value=f"🔥 {user['streak_days']} days", inline=True)
        
        await ctx.send(embed=embed)
    
    # ═══════════════════════════════════════════════════════════════════
    # FORGE BLOOM COMMAND
    # ═══════════════════════════════════════════════════════════════════
    
    @commands.command(name="gforge")
    async def forge(self, ctx):
        """Forge a Bloom from Petals and Seeds"""
        user = get_or_create_user(str(ctx.author.id), ctx.author.display_name)
        
        if user["petals"] < BLOOM_FORGE_COST_PETALS:
            await ctx.send(f"❌ You need {BLOOM_FORGE_COST_PETALS} {PETAL_EMOJI} Petals to forge a Bloom!")
            return
        
        if user["seeds"] < BLOOM_FORGE_COST_SEEDS:
            await ctx.send(f"❌ You need {BLOOM_FORGE_COST_SEEDS} {SEED_EMOJI} Seeds to forge a Bloom!")
            return
        
        # Deduct costs
        update_currency(str(ctx.author.id), "petals", -BLOOM_FORGE_COST_PETALS, "Forge Bloom", "forge")
        update_currency(str(ctx.author.id), "seeds", -BLOOM_FORGE_COST_SEEDS, "Forge Bloom", "forge")
        update_currency(str(ctx.author.id), "blooms", 1, "Forge Bloom", "forge")
        
        embed = discord.Embed(
            title=f"{BLOOM_EMOJI} Bloom Forged!",
            description=(
                f"You've successfully forged a **Bloom**!\n\n"
                f"**Cost:**\n"
                f"-{BLOOM_FORGE_COST_PETALS} {PETAL_EMOJI} Petals\n"
                f"-{BLOOM_FORGE_COST_SEEDS} {SEED_EMOJI} Seeds\n\n"
                f"**New Balance:**\n"
                f"+1 {BLOOM_EMOJI} Bloom"
            ),
            color=0xe91e63
        )
        
        await ctx.send(embed=embed)
    
    # ═══════════════════════════════════════════════════════════════════
    # LEADERBOARD COMMAND
    # ═══════════════════════════════════════════════════════════════════
    
    @commands.command(name="gleaderboard", aliases=["glb", "gtop"])
    async def leaderboard(self, ctx):
        """View top earners"""
        with get_conn() as conn:
            top_users = conn.execute("""
                SELECT discord_id, username, total_earned, tier, petals, seeds, blooms
                FROM users
                ORDER BY total_earned DESC
                LIMIT 10
            """).fetchall()
        
        embed = discord.Embed(
            title="🏆 Garden Leaderboard",
            description="Top earners in the garden!",
            color=0xf1c40f
        )
        
        for i, user in enumerate(top_users, 1):
            emoji = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
            
            embed.add_field(
                name=f"{emoji} {user['username']}",
                value=(
                    f"**Earned:** {user['total_earned']} • **Tier:** {user['tier']}\n"
                    f"{PETAL_EMOJI} {user['petals']} • {SEED_EMOJI} {user['seeds']} • {BLOOM_EMOJI} {user['blooms']}"
                ),
                inline=False
            )
        
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(GardenEconomyCog(bot))


# ═══════════════════════════════════════════════════════════════════════
# INSTALLATION NOTES
# ═══════════════════════════════════════════════════════════════════════
"""
1. Copy this file to /root/cheru/cogs/garden_economy.py

2. Update configuration at the top:
   - Set channel IDs (SHOP_CHANNEL_ID, DROPS_CHANNEL_ID, ORDERS_CHANNEL_ID)
   - Set role IDs (VIP_ROLE_ID, TIER_ROLES)
   - Add custom emoji IDs

3. Create data directory:
   mkdir -p /root/cheru/data

4. Load in cheru.py:
   await bot.load_extension("cogs.garden_economy")

5. Commands:
   .gdaily / .gd - Claim daily rewards
   .gbalance / .gbal - Check balance
   .gforge - Forge a Bloom
   .gleaderboard / .glb - View leaderboard

6. TODO - Add these features next:
   - Shop system (.gshop, .gbuy)
   - Task completion
   - Gifting system
   - Drop claiming
   - Admin commands
"""