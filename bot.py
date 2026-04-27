import logging
import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ======================================================
# COLE SEU TOKEN DO BOT AQUI
BOT_TOKEN = "8739383957:AAHrgxE_6rhf7R3IZIcBfoyQVarghk0fxrI"
# ======================================================

GARDEN_URL = "https://chainaid.app/inurl/"

# Estados da conversa
SELECT_ACTION, ENTER_AMOUNT, CONFIRM_ORDER = range(3)

# Taxas (atualizar regularmente)
PIX_TO_DEPIX_RATE = 1.0  # 1 Pix = 1 DePix

# Configuração de taxa
FEE_PERCENT = 0.001  # 0.1%
FEE_MIN = 1  # mínimo 1 unidade

# DePix é atrelado 1:1 ao Real Brasileiro (BRL)
# Usando taxa aproximada fixa, pois DePix não tem API direta
DEPIX_TO_USD_RATE = 0.17  # 1 BRL ≈ $0.17 USD (atualizar conforme necessário)


# ─── Funções da API ──────────────────────────────────────────────────────────

async def get_btc_price_usd() -> float:
    """
    Busca o preço atual do Bitcoin em USD da API CoinGecko.
    Não é necessário chave de API para endpoints públicos.
    Docs: https://www.coingecko.com/en/api
    """
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {
        "ids": "bitcoin",
        "vs_currencies": "usd",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    btc_price = data["bitcoin"]["usd"]
                    logger.info(f"Preço do BTC obtido: ${btc_price}")
                    return float(btc_price)
                else:
                    logger.warning(f"API CoinGecko retornou status {response.status}")
                    # Fallback: usar último preço conhecido do BTC
                    return 77686.0
    except Exception as e:
        logger.error(f"Erro ao buscar preço do BTC: {e}")
        # Preço de fallback caso a API falhe
        return 77686.0


def calculate_depix_to_lbtc_rate(btc_price_usd: float) -> float:
    """
    Calcula a taxa DePix para L-BTC.
    L-BTC é atrelado 1:1 ao BTC na Rede Liquid.
    DePix é atrelado 1:1 ao BRL (~$0.17 USD).
    """
    if btc_price_usd <= 0:
        return 0.000002188  # taxa de fallback
    rate = DEPIX_TO_USD_RATE / btc_price_usd
    return rate


# ─── Teclados ────────────────────────────────────────────────────────────────

def get_start_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Bridge", callback_data="action_bridge"),
            InlineKeyboardButton("Exchange", callback_data="action_exchange"),
        ],
        [
            InlineKeyboardButton("Suporte", url="https://t.me/your_support_handle"),
        ],
    ])


def get_confirm_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Confirmar – Prosseguir",
                web_app=WebAppInfo(url=GARDEN_URL),
            ),
        ],
        [
            InlineKeyboardButton("❌ Cancelar", callback_data="confirm_no"),
        ],
    ])


def get_back_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Voltar ao Início", callback_data="back_start")]
    ])


# ─── Handlers ────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()

    text = (
        "*O que este bot pode fazer?*\n\n"
        "*Bridge* Pix → DePix ou *Exchange* DePix → L-BTC na menor taxa possível "
        "— sem depender de ninguém.\n\n"
        "Nosso código é aberto. Clique em *Bridge* ou *Exchange* para saber mais e começar.\n\n"
        "Se precisar de ajuda, entre em contato com o *Suporte*."
    )

    if update.message:
        await update.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=get_start_keyboard(),
        )
    elif update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=get_start_keyboard(),
        )

    return SELECT_ACTION


async def select_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "back_start":
        return await start(update, context)

    action = query.data.replace("action_", "")
    context.user_data["action"] = action

    if action == "bridge":
        await query.edit_message_text(
            "*Bridge – Pix → DePix*\n\n"
            "Qual valor em *Pix* você deseja converter para *DePix*?\n\n"
            "_(Responda com um número, ex: `500`)_",
            parse_mode="Markdown",
            reply_markup=get_back_keyboard(),
        )
    else:
        await query.edit_message_text(
            "*Exchange – DePix → L-BTC*\n\n"
            "Qual valor em *DePix* você deseja trocar por *L-BTC*?\n\n"
            "_(Responda com um número, ex: `500`)_",
            parse_mode="Markdown",
            reply_markup=get_back_keyboard(),
        )

    return ENTER_AMOUNT


async def enter_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip().replace(",", ".")
    action = context.user_data.get("action", "bridge")

    try:
        amount = float(text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ Por favor, digite um número positivo válido.",
            reply_markup=get_back_keyboard(),
        )
        return ENTER_AMOUNT

    # --- Taxa ---
    fee = max(round(amount * FEE_PERCENT, 2), FEE_MIN)
    amount_after_fee = round(amount - fee, 2)

    if amount_after_fee <= 0:
        await update.message.reply_text(
            f"❌ Valor muito baixo após a taxa mínima de {FEE_MIN} unidade(s).\n"
            "Por favor, digite um valor maior.",
            parse_mode="Markdown",
            reply_markup=get_back_keyboard(),
        )
        return ENTER_AMOUNT

    # --- Buscar preço do BTC em tempo real e calcular taxa ---
    if action == "bridge":
        from_unit = "Pix"
        to_unit = "DePix"
        rate = PIX_TO_DEPIX_RATE
        rate_str = "1 Pix ≈ 1 DePix"
        action_label = "Bridge"
        est_time = "~15 min"
    else:
        from_unit = "DePix"
        to_unit = "L-BTC"
        
        # Buscar preço do BTC em tempo real da CoinGecko
        btc_price_usd = await get_btc_price_usd()
        rate = calculate_depix_to_lbtc_rate(btc_price_usd)
        
        rate_str = f"1 DePix ≈ {rate:.8f} L-BTC (BTC @ ${btc_price_usd:,.2f})"
        action_label = "Exchange"
        est_time = "~32 min"

    you_receive = round(amount_after_fee * rate, 8)

    context.user_data.update({
        "amount": amount,
        "fee": fee,
        "amount_after_fee": amount_after_fee,
        "you_receive": you_receive,
        "from_unit": from_unit,
        "to_unit": to_unit,
        "rate_str": rate_str,
        "action_label": action_label,
    })

    await update.message.reply_text(
        f"📊 *Resumo do {action_label}*\n\n"
        f"Valor informado: `{amount:.2f}` {from_unit}\n"
        f"Sua taxa (0.1% | mín. {FEE_MIN}): `{fee:.2f}` {from_unit}\n"
        f"Valor repassado: `{amount_after_fee:.2f}` {from_unit}\n"
        f"Taxa: _{rate_str}_\n\n"
        f"Você receberá: ~`{you_receive:.8f}` {to_unit}\n"
        f"Tempo estimado: {est_time}\n\n"
        f"Toque em *Confirmar* para prosseguir _(abre dentro do Telegram)_, "
        f"ou *Cancelar* para abortar.",
        parse_mode="Markdown",
        reply_markup=get_confirm_keyboard(),
    )
    return CONFIRM_ORDER


async def confirm_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "❌ Ordem cancelada.\n\nDigite /start para iniciar uma nova transação."
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "❌ Cancelado. Digite /start para iniciar uma nova transação."
    )
    return ConversationHandler.END


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Não entendi isso. Digite /start para começar."
    )


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SELECT_ACTION: [
                CallbackQueryHandler(select_action, pattern="^action_"),
                CallbackQueryHandler(select_action, pattern="^back_start$"),
            ],
            ENTER_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_amount),
                CallbackQueryHandler(select_action, pattern="^back_start$"),
            ],
            CONFIRM_ORDER: [
                CallbackQueryHandler(confirm_cancel, pattern="^confirm_no$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(conv_handler)
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    logger.info("Bot está rodando...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
