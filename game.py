"""Attribute Arena — движок карточной игры (без зависимостей от Telegram)."""
from __future__ import annotations

import random
from dataclasses import dataclass

ATTR_ICON = {"str": "💪", "agi": "🏹", "int": "🔮", "uni": "⭐"}
ATTR_NAME = {"str": "Сила", "agi": "Ловкость", "int": "Интеллект", "uni": "Универсал"}
HERO_HP = {"str": 34, "agi": 32, "int": 26}
HERO_DESC = {
    "str": "34❤️, стойкий",
    "agi": "32❤️, +1 карта в начале",
    "int": "26❤️, мощные заклинания",
}
ULTS = {
    "str": ("Несокрушимость", "герой +8❤️, твои существа +2 к здоровью"),
    "agi": ("Шквал", "твои существа +1 к атаке и могут атаковать сразу"),
    "int": ("Метеор", "4 урона всем существам врага и его герою"),
}
MAX_BOARD, MAX_HAND, MAX_MANA, ULT_CHARGE = 5, 7, 10, 3


@dataclass(frozen=True)
class Card:
    key: str
    name: str
    attr: str
    cost: int
    kind: str  # "minion" | "spell"
    atk: int = 0
    hp: int = 0
    kw: tuple = ()  # "taunt" (провокация), "haste" (рывок)
    fx: str = ""
    text: str = ""


def M(key, name, attr, cost, atk, hp, kw=(), fx="", text=""):
    return Card(key, name, attr, cost, "minion", atk, hp, tuple(kw), fx, text)


def S(key, name, attr, cost, fx, text):
    return Card(key, name, attr, cost, "spell", 0, 0, (), fx, text)


CARDS = [
    # 💪 Сила
    M("boar", "Вепрь", "str", 1, 1, 3, ["taunt"], text="Провокация"),
    M("shield", "Щитоносец", "str", 2, 2, 4, ["taunt"], text="Провокация"),
    M("brute", "Громила", "str", 3, 3, 5),
    M("guard", "Каменный страж", "str", 4, 3, 7, ["taunt"], text="Провокация"),
    M("titan", "Титан", "str", 6, 6, 8),
    S("heal", "Исцеление", "str", 2, "heal6", "Герой +6❤️"),
    S("hammer", "Удар молота", "str", 3, "smash", "4 урона сильнейшему врагу"),
    S("rally", "Боевой клич", "str", 3, "buff_all", "Твои существа +1/+1"),
    # 🏹 Ловкость
    M("blade", "Теневой клинок", "agi", 1, 2, 1, ["haste"], text="Рывок"),
    M("hunter", "Охотник", "agi", 2, 3, 3),
    M("raider", "Налётчик", "agi", 3, 4, 2, ["haste"], text="Рывок"),
    M("ghost", "Призрак", "agi", 4, 5, 4),
    M("assassin", "Убийца", "agi", 5, 7, 3, ["haste"], text="Рывок"),
    S("volley", "Град стрел", "agi", 3, "volley", "2 урона всем существам врага"),
    S("crit", "Критический удар", "agi", 2, "face4", "4 урона герою врага"),
    # 🔮 Интеллект
    M("pupil", "Ученик", "int", 1, 1, 2, fx="draw1", text="Возьми карту"),
    M("frost", "Маг льда", "int", 3, 2, 4, fx="ping2", text="2 урона герою врага"),
    M("witch", "Колдунья", "int", 4, 3, 4, fx="zap2", text="2 урона случайному существу врага"),
    S("fireball", "Огненный шар", "int", 5, "fireball", "6 урона сильнейшему врагу"),
    S("surge", "Всплеск маны", "int", 1, "mana2", "+2 маны сейчас"),
    S("wisdom", "Мудрость", "int", 2, "draw2", "Возьми 2 карты"),
    S("nova", "Взрыв", "int", 6, "nova", "3 урона всем врагам"),
    # ⭐ Универсальные
    M("merc", "Наёмник", "uni", 2, 2, 3),
    M("courier", "Курьер", "uni", 2, 1, 1, ["haste"], fx="draw1", text="Рывок. Возьми карту"),
    M("golem", "Голем", "uni", 5, 5, 5),
    S("potion", "Зелье", "uni", 1, "heal3", "Герой +3❤️"),
]


@dataclass(eq=False)
class Minion:
    card: Card
    atk: int
    hp: int
    max_hp: int
    can_attack: bool

    @property
    def taunt(self) -> bool:
        return "taunt" in self.card.kw


def make_deck(attr: str) -> list:
    deck, others = [], []
    for c in CARDS:
        if c.attr in (attr, "uni"):
            deck += [c, c]
        else:
            others.append(c)
    deck += random.sample(others, 4)  # немного карт чужих атрибутов
    random.shuffle(deck)
    return deck


class Player:
    def __init__(self, uid: int, name: str, attr: str, is_bot: bool = False):
        self.uid, self.name, self.attr, self.is_bot = uid, name, attr, is_bot
        self.max_hp = HERO_HP[attr]
        self.hp = self.max_hp
        self.max_mana = 0
        self.mana = 0
        self.deck = make_deck(attr)
        self.hand: list = []
        self.board: list = []
        self.charge = 0
        self.fatigue = 0


class Game:
    def __init__(self, p0: Player, p1: Player):
        self.players = [p0, p1]
        self.winner = None  # None | 0 | 1 | 2 (ничья)
        self.turn_no = 0
        self.log: list = []
        # поля для Telegram-слоя
        self.gid = ""
        self.chat_ids = [None, None]
        self.msg_ids = [None, None]
        self.sel = [None, None]

        first = random.randint(0, 1)
        self.turn = first
        for i, p in enumerate(self.players):
            n = (3 if i == first else 4) + (1 if p.attr == "agi" else 0)
            self.draw(i, n, quiet=True)
        self._start_turn(first)

    # ---------- служебное ----------
    def _log(self, s: str):
        self.log.append(s)
        self.log = self.log[-12:]

    def _can_act(self, pi: int):
        if self.winner is not None:
            return "Игра окончена"
        if self.turn != pi:
            return "Сейчас ход соперника"
        return None

    def _cleanup(self):
        for p in self.players:
            p.board = [m for m in p.board if m.hp > 0]

    def _check(self):
        if self.winner is not None:
            return
        a, b = (p.hp <= 0 for p in self.players)
        if a and b:
            self.winner = 2
        elif a:
            self.winner = 1
        elif b:
            self.winner = 0

    def draw(self, pi: int, n: int, quiet: bool = False):
        p = self.players[pi]
        for _ in range(n):
            if not p.deck:
                p.fatigue += 1
                p.hp -= p.fatigue
                if not quiet:
                    self._log(f"{p.name}: колода пуста, усталость −{p.fatigue}❤️")
                continue
            c = p.deck.pop()
            if len(p.hand) < MAX_HAND:
                p.hand.append(c)
            elif not quiet:
                self._log(f"{p.name}: рука полна, «{c.name}» сгорает")

    def _start_turn(self, pi: int):
        p = self.players[pi]
        self.turn_no += 1
        p.max_mana = min(MAX_MANA, p.max_mana + 1)
        p.mana = p.max_mana
        for m in p.board:
            m.can_attack = True
        self._log(f"— Раунд {self.turn_no}: ходит {p.name}")
        self.draw(pi, 1)
        self._check()

    def _strongest(self, op: Player):
        if not op.board:
            return None
        return max(op.board, key=lambda m: (m.atk, m.hp))

    def _hit_strong(self, op: Player, n: int):
        t = self._strongest(op)
        if t:
            t.hp -= n
            self._log(f"  → «{t.card.name}» получает {n}")
        else:
            op.hp -= n
            self._log(f"  → {op.name} получает {n}")

    # ---------- эффекты ----------
    def _fx(self, pi: int, fx: str):
        me, op = self.players[pi], self.players[1 - pi]
        if fx == "heal6":
            me.hp = min(me.max_hp, me.hp + 6)
        elif fx == "heal3":
            me.hp = min(me.max_hp, me.hp + 3)
        elif fx == "smash":
            self._hit_strong(op, 4)
        elif fx == "fireball":
            self._hit_strong(op, 6)
        elif fx == "buff_all":
            for m in me.board:
                m.atk += 1
                m.hp += 1
                m.max_hp += 1
        elif fx == "volley":
            for m in op.board:
                m.hp -= 2
        elif fx == "face4":
            op.hp -= 4
        elif fx == "ping2":
            op.hp -= 2
        elif fx == "zap2":
            if op.board:
                random.choice(op.board).hp -= 2
        elif fx == "draw1":
            self.draw(pi, 1)
        elif fx == "draw2":
            self.draw(pi, 2)
        elif fx == "mana2":
            me.mana = min(MAX_MANA + 2, me.mana + 2)
        elif fx == "nova":
            for m in op.board:
                m.hp -= 3
            op.hp -= 3

    # ---------- действия игрока ----------
    def play_card(self, pi: int, hi: int):
        err = self._can_act(pi)
        if err:
            return err
        p = self.players[pi]
        if not 0 <= hi < len(p.hand):
            return "Нет такой карты"
        c = p.hand[hi]
        if c.cost > p.mana:
            return "Не хватает маны"
        if c.kind == "minion" and len(p.board) >= MAX_BOARD:
            return "Поле заполнено"
        p.mana -= c.cost
        p.hand.pop(hi)
        if c.attr == p.attr:
            p.charge = min(ULT_CHARGE, p.charge + 1)
        if c.kind == "minion":
            p.board.append(Minion(c, c.atk, c.hp, c.hp, "haste" in c.kw))
            self._log(f"{p.name} призывает «{c.name}»")
        else:
            self._log(f"{p.name} применяет «{c.name}»")
        if c.fx:
            self._fx(pi, c.fx)
        self._cleanup()
        self._check()
        return None

    def legal_targets(self, pi: int, mi: int) -> list:
        me, op = self.players[pi], self.players[1 - pi]
        if not 0 <= mi < len(me.board):
            return []
        taunts = [j for j, m in enumerate(op.board) if m.taunt]
        if taunts:
            return taunts
        return [-1] + list(range(len(op.board)))

    def attack(self, pi: int, mi: int, target: int):
        err = self._can_act(pi)
        if err:
            return err
        me, op = self.players[pi], self.players[1 - pi]
        if not 0 <= mi < len(me.board):
            return "Нет такого существа"
        m = me.board[mi]
        if not m.can_attack or m.atk <= 0:
            return "Это существо не может атаковать"
        if target not in self.legal_targets(pi, mi):
            return "Сначала нужно уничтожить существо с провокацией 🛡"
        m.can_attack = False
        if target == -1:
            op.hp -= m.atk
            self._log(f"«{m.card.name}» бьёт героя: −{m.atk}❤️")
        else:
            t = op.board[target]
            t.hp -= m.atk
            m.hp -= t.atk
            self._log(f"«{m.card.name}» атакует «{t.card.name}»")
        self._cleanup()
        self._check()
        return None

    def ultimate(self, pi: int):
        err = self._can_act(pi)
        if err:
            return err
        me, op = self.players[pi], self.players[1 - pi]
        if me.charge < ULT_CHARGE:
            return "Ульта ещё не заряжена"
        name = ULTS[me.attr][0]
        self._log(f"💥 {me.name} использует «{name}»!")
        if me.attr == "str":
            me.hp = min(me.max_hp + 8, me.hp + 8)
            for m in me.board:
                m.hp += 2
                m.max_hp += 2
        elif me.attr == "agi":
            for m in me.board:
                m.atk += 1
                m.can_attack = True
        elif me.attr == "int":
            for m in op.board:
                m.hp -= 4
            op.hp -= 4
        me.charge = 0
        self._cleanup()
        self._check()
        return None

    def end_turn(self, pi: int):
        err = self._can_act(pi)
        if err:
            return err
        self.sel[pi] = None
        self.turn = 1 - pi
        self._start_turn(self.turn)
        return None

    def surrender(self, pi: int):
        if self.winner is None:
            self.winner = 1 - pi
            self._log(f"{self.players[pi].name} сдаётся")


# ---------- простой ИИ ----------
def _ai_card_ok(game: Game, pi: int, c: Card) -> bool:
    me, op = game.players[pi], game.players[1 - pi]
    if c.kind == "minion":
        return len(me.board) < MAX_BOARD
    fx = c.fx
    if fx == "heal6":
        return me.hp <= me.max_hp - 4
    if fx == "heal3":
        return me.hp <= me.max_hp - 2
    if fx == "buff_all":
        return len(me.board) >= 2
    if fx == "volley":
        return len(op.board) >= 2
    if fx == "nova":
        return len(op.board) >= 2 or op.hp <= 3
    if fx in ("draw1", "draw2"):
        return len(me.hand) <= 5
    if fx == "mana2":
        return any(o is not c and me.mana < o.cost <= me.mana + 1 for o in me.hand)
    return True


def _ai_ult_ok(game: Game, pi: int) -> bool:
    me, op = game.players[pi], game.players[1 - pi]
    if me.attr == "str":
        return me.hp <= me.max_hp - 6 or len(me.board) >= 2
    if me.attr == "agi":
        return len(me.board) >= 2
    return len(op.board) >= 2 or op.hp <= 4


def ai_turn(game: Game, pi: int):
    me, op = game.players[pi], game.players[1 - pi]
    for _ in range(40):
        if game.winner is not None or game.turn != pi:
            return
        if me.charge >= ULT_CHARGE and _ai_ult_ok(game, pi):
            game.ultimate(pi)
            continue
        best = None
        for i, c in enumerate(me.hand):
            if c.cost <= me.mana and _ai_card_ok(game, pi, c):
                if best is None or c.cost > me.hand[best].cost:
                    best = i
        if best is None:
            break
        game.play_card(pi, best)

    if game.winner is None and game.turn == pi:
        ready = [m for m in me.board if m.can_attack and m.atk > 0]
        no_taunt = not any(m.taunt for m in op.board)
        if no_taunt and ready and sum(m.atk for m in ready) >= op.hp:
            for m in ready:
                if m in me.board:
                    game.attack(pi, me.board.index(m), -1)
        else:
            for m in list(me.board):
                if game.winner is not None:
                    break
                if m not in me.board or not m.can_attack or m.atk <= 0:
                    continue
                mi = me.board.index(m)
                targets = game.legal_targets(pi, mi)
                tm = [(j, op.board[j]) for j in targets if j >= 0]
                safe = [(j, t) for j, t in tm if t.hp <= m.atk and t.atk < m.hp]
                trade = [(j, t) for j, t in tm if t.hp <= m.atk and t.card.cost >= m.card.cost]
                if safe:
                    j = max(safe, key=lambda x: (x[1].atk, x[1].hp))[0]
                elif trade:
                    j = max(trade, key=lambda x: x[1].card.cost)[0]
                elif -1 in targets:
                    j = -1
                elif tm:
                    j = tm[0][0]
                else:
                    continue
                game.attack(pi, mi, j)

    if game.winner is None and game.turn == pi:
        game.end_turn(pi)
