import discord
import os
import random
import asyncio
from dotenv import load_dotenv
from collections import defaultdict, Counter

# ======================================================================
# CONFIGURACIÓN INICIAL Y ESTRUCTURAS DE DATOS
# ======================================================================
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
client = discord.Client(intents=intents)

partidas = {}  # {channel_id: partida}

class Rol:
    MAFIOSO = "Mafioso"
    DETECTIVE = "Detective"
    MEDICO = "Médico"
    CIUDADANO = "Ciudadano"


# ======================================================================
#  ASIGNACIÓN DE ROLES(ES PARTE DE CREACIÓN DE PARTIDAS)
# ======================================================================
async def asignar_roles(channel):
    partida = partidas[channel.id]
    jugadores = list(partida["jugadores"].keys())
    random.shuffle(jugadores)

    num_mafiosos = max(1, len(jugadores) // 3)
    partida["mafiosos"] = jugadores[:num_mafiosos]
    partida["jugadores_vivos"] = partida["jugadores"].copy()

    if len(jugadores) > num_mafiosos:
        partida["detective"] = jugadores[num_mafiosos]
    if len(jugadores) > num_mafiosos + 1:
        partida["medico"] = jugadores[num_mafiosos + 1]

    for i, jugador_id in enumerate(jugadores):
        if i < num_mafiosos:
            rol = Rol.MAFIOSO
        elif i == num_mafiosos and "detective" in partida:
            rol = Rol.DETECTIVE
        elif i == num_mafiosos + 1 and "medico" in partida:
            rol = Rol.MEDICO
        else:
            rol = Rol.CIUDADANO

        user = await client.fetch_user(jugador_id)
        mensaje = f"🎭 **Tu rol es:** {rol}\n"
        if rol == Rol.MAFIOSO:
            mensaje += "🔪 Debes matar a los ciudadanos por la noche.\n"
            otros_mafiosos = [partida["jugadores"][id] for id in partida["mafiosos"] if id != jugador_id]
            if otros_mafiosos:
                mensaje += f"👥 Tus compañeros mafiosos son: {', '.join(otros_mafiosos)}\n"
        elif rol == Rol.DETECTIVE:
            mensaje += "🕵️ Puedes investigar a un jugador cada noche para descubrir su rol.\n"
        elif rol == Rol.MEDICO:
            mensaje += "💊 Puedes proteger a un jugador cada noche de los ataquen.\n"
        else:
            mensaje += "👁️ Debes descubrir a los mafiosos y votar durante el día.\n"

        await user.send(mensaje)

    await channel.send("✅ **¡La partida comienza!** 🌙 Ha caido la noche...")
    await iniciar_votacion_noche(partida, channel)


# ======================================================================
# VISTAS Y COMPONENTES PARA VOTACIONES
# ======================================================================
class VotoNocheView(discord.ui.View):
    def __init__(self, partida, rol, jugador_id):
        super().__init__(timeout=40.0)
        self.partida = partida
        self.rol = rol
        self.jugador_id = jugador_id
        self.voto = None
        self.ya_voto = False

        for target_id, nombre in partida["jugadores_vivos"].items():
            if target_id != jugador_id:
                if rol == Rol.MAFIOSO and target_id not in partida["mafiosos"]:
                    self.add_item(VotoButton(target_id, nombre, "🔪 Matar a"))
                elif rol == Rol.DETECTIVE:
                    self.add_item(VotoButton(target_id, nombre, "🕵️ Investigar a"))
                elif rol == Rol.MEDICO:
                    self.add_item(VotoButton(target_id, nombre, "💊 Proteger a"))

    async def contar_votos(self):
        return self.voto


class VotoDiaView(discord.ui.View):
    def __init__(self, partida, jugador_actual_id):
        super().__init__(timeout=40.0)
        self.partida = partida
        self.votos = defaultdict(int)
        self.votantes = set()

        for jugador_id, nombre in partida["jugadores_vivos"].items():
            if jugador_id != jugador_actual_id:
                self.add_item(VotoButton(jugador_id, nombre, "⚖️ Acusar a"))

    async def contar_votos(self):
        if not self.votos:
            return None
        return max(self.votos.items(), key=lambda x: x[1])[0]


class VotoButton(discord.ui.Button):
    def __init__(self, jugador_id, nombre_jugador, accion):
        super().__init__(label=f"{accion} {nombre_jugador}", style=discord.ButtonStyle.danger)
        self.jugador_id = jugador_id

    async def callback(self, interaction: discord.Interaction):
        partida = None
        for p in partidas.values():
            if interaction.user.id in p["jugadores"]:
                partida = p
                break

        if not partida or interaction.user.id not in partida["jugadores_vivos"]:
            await interaction.response.send_message("⚠️ Ya no sos parte del juego.", ephemeral=True)
            return

        if interaction.user.id == self.jugador_id:
            await interaction.response.send_message("⚠️ No podes votarte a vos mismo.", ephemeral=True)
            return

        if isinstance(self.view, VotoDiaView):
            if interaction.user.id in self.view.votantes:
                await interaction.response.send_message("⚠️ Ya has votado en esta ronda.", ephemeral=True)
                return
            self.view.votantes.add(interaction.user.id)
            self.view.votos[self.jugador_id] += 1
        else:
            if self.view.ya_voto:
                await interaction.response.send_message("⚠️ Ya has votado en esta ronda.", ephemeral=True)
                return
            self.view.ya_voto = True
            self.view.voto = self.jugador_id

        await interaction.response.send_message(f"✅ Voto registrado: {self.label}", ephemeral=True)


# ======================================================================
# FASE NOCHE
# ======================================================================
async def iniciar_votacion_noche(partida, channel):
    resultados_noche = {"atacado": None, "protegido": None, "investigado": None}
    partida["_mafia_views"] = []
    partida["_detective_view"] = None
    partida["_medico_view"] = None

    tasks = []
    
    # Mafiosos votan
    for mafioso_id in [m for m in partida["mafiosos"] if m in partida["jugadores_vivos"]]:
        view = VotoNocheView(partida, Rol.MAFIOSO, mafioso_id)
        partida["_mafia_views"].append(view)
        tasks.append(client.get_user(mafioso_id).send("🌙 **Fase nocturna** - Vota a quién queres matar:", view=view))

    # Detective vota
    if partida.get("detective") and partida["detective"] in partida["jugadores_vivos"]:
        view = VotoNocheView(partida, Rol.DETECTIVE, partida["detective"])
        partida["_detective_view"] = view
        tasks.append(client.get_user(partida["detective"]).send("🕵️ **Sos el detective** - ¿A quién investigas?", view=view))

    # Médico vota
    if partida.get("medico") and partida["medico"] in partida["jugadores_vivos"]:
        view = VotoNocheView(partida, Rol.MEDICO, partida["medico"])
        partida["_medico_view"] = view
        tasks.append(client.get_user(partida["medico"]).send("💊 **Sos el médico** - ¿A quién proteges?", view=view))

    await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.sleep(60)

    # Procesar votos mafiosos
    votos_mafia = defaultdict(int)
    for view in partida["_mafia_views"]:
        if view.voto:
            votos_mafia[view.voto] += 1
    if votos_mafia:
        resultados_noche["atacado"] = max(votos_mafia.items(), key=lambda x: x[1])[0]

    # Procesar voto detective
    if partida.get("_detective_view") and partida["_detective_view"].voto:
        resultados_noche["investigado"] = partida["_detective_view"].voto

    # Procesar voto médico
    if partida.get("_medico_view") and partida["_medico_view"].voto:
        resultados_noche["protegido"] = partida["_medico_view"].voto

    # Limpiar vistas temporales
    partida.pop("_mafia_views", None)
    partida.pop("_detective_view", None)
    partida.pop("_medico_view", None)

    await iniciar_fase_dia(channel, partida, resultados_noche)


# ======================================================================
# 5. FASE DIA - RESULTADOS Y VOTACIÓN GENERAL
# ======================================================================
async def iniciar_fase_dia(channel, partida, resultados_noche):
    reporte = "🌅 **¡Amanece en el pueblo!**\n"

    # Mostrar acciones nocturnas
    if resultados_noche["atacado"]:
        reporte += f"😈 La mafia intentó asesinar a {partida['jugadores'].get(resultados_noche['atacado'], 'alguien')}.\n"
    else:
        reporte += "😈 La mafia no atacó a nadie esta noche.\n"

    if resultados_noche["protegido"]:
        reporte += f"🩺 El médico intentó salvar a {partida['jugadores'].get(resultados_noche['protegido'], 'alguien')}.\n"

    if resultados_noche["investigado"]:
        rol_real = await obtener_rol_jugador(partida, resultados_noche["investigado"])
        reporte += f"🔍 El detective investigó a {partida['jugadores'].get(resultados_noche['investigado'], 'alguien')} y descubrió que es **{rol_real}**.\n"

    # Procesar eliminaciones
    eliminados = []
    if (resultados_noche["atacado"] and resultados_noche["protegido"] != resultados_noche["atacado"]):
        nombre, rol = await procesar_eliminacion(partida, resultados_noche["atacado"])
        eliminados.append((nombre, rol, "asesinado por la mafia"))

    if (resultados_noche["investigado"] and resultados_noche["protegido"] != resultados_noche["investigado"]):
        nombre, rol = await procesar_eliminacion(partida, resultados_noche["investigado"])
        eliminados.append((nombre, rol, "eliminado por el detective"))

    for nombre, rol, razon in eliminados:
        reporte += f"\n💀 **{nombre}** fue {razon}! (Era **{rol}**)\n"

    # Mostrar jugadores vivos
    vivos_restantes = list(partida["jugadores_vivos"].values())
    reporte += f"\n👥 **Jugadores vivos:** {', '.join(vivos_restantes) if vivos_restantes else 'Ninguno'}"
    await channel.send(reporte)

    # Verificar fin del juego
    if await verificar_fin_partida(channel, partida):
        return

    await asyncio.sleep(5)
    await iniciar_votacion_dia(channel, partida)

# Votacion general de quién creen que es un mafioso 

async def iniciar_votacion_dia(channel, partida):
    view = VotoDiaView(partida, None)
    mensaje = await channel.send(
        "☀️ **Fase dia - Votación pública**\n"
        f"Jugadores vivos: {', '.join(partida['jugadores_vivos'].values())}\n"
        "Voten por quién quieren eliminar (no pueden votarse a sí mismos).",
        view=view,
    )

    await asyncio.sleep(60)
    eliminado = await view.contar_votos()

    if not eliminado:
        await channel.send("😶 Nadie fue eliminado hoy.")
    else:
        nombre, rol = await procesar_eliminacion(partida, eliminado)
        await channel.send(f"⚖️ **{nombre}** fue eliminado por el pueblo! (Era **{rol}**)")

    if not await verificar_fin_partida(channel, partida):
        await channel.send("🌙 **¡Es de noche!**")
        await asyncio.sleep(3)
        await iniciar_votacion_noche(partida, channel)


# ======================================================================
# DETERMINAR GANADORES Y MANEJAR ELIMINACIONES
# ======================================================================
async def verificar_fin_partida(channel, partida):
    if not partida["mafiosos"]:
        await channel.send("🎉 **¡Los ciudadanos ganan!** Todos los mafiosos fueron eliminados.")
        partidas.pop(channel.id, None)
        return True

    vivos_restantes = len(partida["jugadores_vivos"])
    mafiosos_vivos = len([m for m in partida["mafiosos"] if m in partida["jugadores_vivos"]])

    if mafiosos_vivos >= vivos_restantes / 2:
        await channel.send("💀 **¡Los mafiosos ganan!** Dominaron el pueblo.")
        partidas.pop(channel.id, None)
        return True

    return False


async def obtener_rol_jugador(partida, jugador_id):
    if jugador_id in partida["mafiosos"]:
        return Rol.MAFIOSO
    elif jugador_id == partida.get("detective"):
        return Rol.DETECTIVE
    elif jugador_id == partida.get("medico"):
        return Rol.MEDICO
    return Rol.CIUDADANO


async def procesar_eliminacion(partida, jugador_id):
    if not jugador_id or jugador_id not in partida["jugadores_vivos"]:
        return None, None

    nombre = partida["jugadores_vivos"].pop(jugador_id)
    rol = await obtener_rol_jugador(partida, jugador_id)

    if jugador_id in partida["mafiosos"]:
        partida["mafiosos"].remove(jugador_id)
    elif jugador_id == partida.get("detective"):
        partida["detective"] = None
    elif jugador_id == partida.get("medico"):
        partida["medico"] = None

    return nombre, rol


# ======================================================================
# ACTIVAR EL BOT
# ======================================================================
@client.event
async def on_ready():
    print(f"✅ Bot conectado como {client.user}")

# ======================================================================
# CREACIÓN DE PARTIDAS
# ======================================================================

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if message.content.startswith("!mafia crear"):
        if message.channel.id in partidas:
            await message.channel.send("⚠️ Ya hay una partida en este canal.")
            return

        try:
            max_jugadores = int(message.content.split()[2]) if len(message.content.split()) > 2 else 8
        except ValueError:
            max_jugadores = 8

        partidas[message.channel.id] = {
            "jugadores": {},
            "max": max(4, min(max_jugadores, 16)),
            "mafiosos": [],
            "detective": None,
            "medico": None,
            "jugadores_vivos": {},
            "creador": message.author.id,
        }
        await message.channel.send(
            f"🎮 **Partida creada por {message.author.name}!** Máximo {partidas[message.channel.id]['max']} jugadores.\n"
            "Usa `!mafia unirme` para unirte o `!mafia ayuda` para ver los comandos."
        )

    elif message.content.startswith("!mafia unirme"):
        partida = partidas.get(message.channel.id)
        if not partida:
            await message.channel.send("⚠️ No hay partida en este canal. Usa `!mafia crear` primero.")
            return

        if len(partida["jugadores"]) >= partida["max"]:
            await message.channel.send("⚠️ La partida está llena.")
            return

        if message.author.id in partida["jugadores"]:
            await message.channel.send("⚠️ Ya estás en la partida.")
            return

        partida["jugadores"][message.author.id] = message.author.name
        partida["jugadores_vivos"][message.author.id] = message.author.name
        await message.channel.send(
            f"✅ **{message.author.name}** se unió a la partida.\n"
            f"👥 Jugadores actuales: {len(partida['jugadores'])}/{partida['max']}"
        )

    elif message.content.startswith("!mafia iniciar"):
        partida = partidas.get(message.channel.id)
        if not partida:
            await message.channel.send("⚠️ No hay partida en este canal.")
            return

        if message.author.id != partida["creador"]:
            await message.channel.send("⚠️ Solo el creador de la partida puede iniciarla.")
            return

        if len(partida["jugadores"]) < 4:
            await message.channel.send("⚠️ Se necesitan al menos 4 jugadores para empezar.")
            return

        await message.channel.send("🔄 **Asignando roles...**")
        await asignar_roles(message.channel)

    elif message.content.startswith("!mafia ayuda"):
        await message.channel.send(
            "📖 **Comandos de Mafia:**\n"
            "`!mafia crear [max=8]` - Crea una partida (4-16 jugadores)\n"
            "`!mafia unirme` - Únete a la partida\n"
            "`!mafia iniciar` - Comienza la partida (solo creador)\n\n"
            "🎭 **Roles:**\n"
            "🔪 **Mafiosos** (1/3 del total) - Matan de noche\n"
            "🕵️ **Detective** (1) - Investiga/elimina de noche\n"
            "💊 **Médico** (1) - Protege de noche\n"
            "👁️ **Ciudadanos** - Votan de día\n\n"
            "⏳ **Flujo del juego:**\n"
            "1. 🌙 Noche: Mafiosos, detective y médico actúan\n"
            "2. 🌅 Amanecer: Se revelan los resultados\n"
            "3. ☀️ Día: Discusión y votación pública\n"
            "4. 🔄 Repetir hasta que un bando gane"
        )

# ======================================================================
# INICIAR EL BOT
# ======================================================================
client.run(TOKEN)