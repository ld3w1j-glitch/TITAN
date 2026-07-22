from __future__ import annotations

import json
import hashlib
import hmac
import math
import os
import re
import secrets
import smtplib
import ssl
import sqlite3
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from functools import wraps
from html import escape as html_escape
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile

from flask import (
    Flask, abort, flash, g, redirect, render_template, request,
    send_file, send_from_directory, session, url_for
)
from dotenv import load_dotenv
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
VOLUME_DIR = Path(os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "")) if os.getenv("RAILWAY_VOLUME_MOUNT_PATH") else None
DB_PATH = Path(os.getenv("DB_PATH", str((VOLUME_DIR / "titan.db") if VOLUME_DIR else (BASE_DIR / "titan.db"))))
UPLOAD_DIR = Path(os.getenv("UPLOAD_PATH", str((VOLUME_DIR / "uploads") if VOLUME_DIR else (BASE_DIR / "uploads"))))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "titan-local-" + secrets.token_hex(16))
app.config.update(
    MAX_CONTENT_LENGTH=8 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("RAILWAY_ENVIRONMENT") is not None,
)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

ALLOWED_IMAGES = {"png", "jpg", "jpeg", "webp"}
VERIFICATION_TTL_MINUTES = 10
VERIFICATION_MAX_ATTEMPTS = 5
VERIFICATION_RESEND_SECONDS = 60
UNDO_TTL_SECONDS = 10 * 60

TRACK_LOAD = "Séries, repetições e carga"
TRACK_REPS = "Séries e repetições"
TRACK_TIME = "Séries e tempo"
TRACK_CARDIO = "Tempo, distância e intensidade"
TRACK_MOBILITY = "Séries, tempo e amplitude"

# Catálogo global V1. Cada item possui uma chave estável para receber imagem,
# vídeo e futuras melhorias sem depender do nome exibido ao usuário.
# Campos: chave, nome, modalidade, grupo principal, músculos secundários,
# região, equipamento, nível, registro e orientação curta.
EXERCISE_CATALOG = (
    # Peitoral
    ("supino_reto", "Supino reto", "Musculação", "Peitoral", "Tríceps, ombro anterior", "Peitoral médio", "Barra e banco", "Intermediário", TRACK_LOAD, "Mantenha escápulas apoiadas, pés firmes e desça a barra com controle."),
    ("supino_inclinado", "Supino inclinado", "Musculação", "Peitoral", "Tríceps, ombro anterior", "Peitoral superior", "Barra e banco inclinado", "Intermediário", TRACK_LOAD, "Use inclinação moderada e mantenha as escápulas firmes no banco."),
    ("supino_declinado", "Supino declinado", "Musculação", "Peitoral", "Tríceps, ombro anterior", "Peitoral inferior", "Barra e banco declinado", "Intermediário", TRACK_LOAD, "Mantenha o corpo estabilizado e controle a barra em toda a amplitude."),
    ("crucifixo_halteres", "Crucifixo com halteres", "Musculação", "Peitoral", "Ombro anterior", "Peitoral médio", "Halteres e banco", "Iniciante", TRACK_LOAD, "Conserve os cotovelos levemente flexionados e evite alongar além do controle."),
    ("crossover_polia", "Crossover na polia", "Musculação", "Peitoral", "Ombro anterior", "Peitoral médio e inferior", "Polia dupla", "Iniciante", TRACK_LOAD, "Aproxime as mãos à frente do corpo sem perder a posição dos ombros."),
    ("peck_deck", "Peck deck", "Musculação", "Peitoral", "Ombro anterior", "Peitoral médio", "Máquina", "Iniciante", TRACK_LOAD, "Ajuste o banco para alinhar os braços ao peito e controle o retorno."),
    ("flexao_bracos", "Flexão de braços", "Calistenia", "Peitoral", "Tríceps, ombro anterior, core", "Peitoral médio", "Peso corporal", "Iniciante", TRACK_REPS, "Mantenha o corpo alinhado e aproxime o peito do chão com controle."),
    ("paralelas_peitoral", "Paralelas com foco no peito", "Calistenia", "Peitoral", "Tríceps, ombro anterior", "Peitoral inferior", "Barras paralelas", "Avançado", TRACK_REPS, "Incline levemente o tronco e use amplitude compatível com seus ombros."),

    # Costas
    ("puxada_alta", "Puxada alta", "Musculação", "Costas", "Bíceps, antebraços", "Grande dorsal", "Polia alta", "Iniciante", TRACK_LOAD, "Puxe os cotovelos para baixo sem balançar o tronco."),
    ("barra_fixa_pronada", "Barra fixa pronada", "Calistenia", "Costas", "Bíceps, antebraços, core", "Grande dorsal", "Barra fixa", "Avançado", TRACK_REPS, "Inicie com as escápulas controladas e evite impulsionar as pernas."),
    ("puxada_neutra", "Puxada com pegada neutra", "Musculação", "Costas", "Bíceps, antebraços", "Grande dorsal", "Polia alta", "Iniciante", TRACK_LOAD, "Conduza os cotovelos para baixo mantendo o peito aberto."),
    ("pulldown_bracos_retos", "Pulldown com braços retos", "Musculação", "Costas", "Tríceps, core", "Grande dorsal", "Polia alta", "Intermediário", TRACK_LOAD, "Mantenha os braços quase estendidos e mova pelos ombros, não pela lombar."),
    ("pullover_halter", "Pullover com halter", "Musculação", "Costas", "Peitoral, tríceps", "Grande dorsal", "Halter e banco", "Intermediário", TRACK_LOAD, "Desça o halter somente até manter ombros e coluna confortáveis."),
    ("remada_unilateral", "Remada unilateral", "Musculação", "Costas", "Bíceps, ombro posterior", "Dorsal e região central", "Halter e banco", "Iniciante", TRACK_LOAD, "Puxe o cotovelo em direção ao quadril sem girar o tronco."),
    ("remada_baixa", "Remada baixa", "Musculação", "Costas", "Bíceps, ombro posterior", "Romboides e trapézio médio", "Polia baixa", "Iniciante", TRACK_LOAD, "Puxe com os cotovelos e controle a volta sem arredondar a coluna."),
    ("remada_curvada", "Remada curvada", "Musculação", "Costas", "Bíceps, lombar, ombro posterior", "Romboides e dorsal", "Barra", "Intermediário", TRACK_LOAD, "Mantenha a coluna neutra e o tronco estável durante a remada."),
    ("remada_cavalinho", "Remada cavalinho", "Musculação", "Costas", "Bíceps, ombro posterior", "Região central", "Barra T ou máquina", "Intermediário", TRACK_LOAD, "Aproxime a carga do tronco sem elevar excessivamente os ombros."),
    ("remada_maquina", "Remada na máquina", "Musculação", "Costas", "Bíceps, ombro posterior", "Romboides e trapézio médio", "Máquina", "Iniciante", TRACK_LOAD, "Ajuste o apoio para manter o peito firme e controle as escápulas."),

    # Trapézio
    ("encolhimento_halteres", "Encolhimento com halteres", "Musculação", "Trapézio", "Antebraços", "Trapézio superior", "Halteres", "Iniciante", TRACK_LOAD, "Eleve os ombros verticalmente sem girá-los para frente ou para trás."),
    ("caminhada_fazendeiro", "Caminhada do fazendeiro", "Funcional", "Trapézio", "Antebraços, core, panturrilhas", "Trapézio superior e pegada", "Halteres ou kettlebells", "Intermediário", TRACK_CARDIO, "Caminhe com postura alta, passos controlados e cargas equilibradas."),
    ("elevacao_y", "Elevação em Y", "Musculação", "Trapézio", "Ombro posterior", "Trapézio médio e inferior", "Halteres leves ou polia", "Intermediário", TRACK_LOAD, "Eleve os braços em Y sem compensar com a lombar ou encolher os ombros."),

    # Ombros
    ("desenvolvimento_militar", "Desenvolvimento militar", "Musculação", "Ombros", "Tríceps, trapézio, core", "Deltoide anterior", "Barra", "Intermediário", TRACK_LOAD, "Mantenha abdômen firme e evite arquear excessivamente a lombar."),
    ("desenvolvimento_arnold", "Desenvolvimento Arnold", "Musculação", "Ombros", "Tríceps", "Deltoide anterior e lateral", "Halteres", "Intermediário", TRACK_LOAD, "Gire os braços de forma controlada e sem perder o alinhamento dos punhos."),
    ("elevacao_frontal", "Elevação frontal", "Musculação", "Ombros", "Peitoral superior", "Deltoide anterior", "Halteres, barra ou polia", "Iniciante", TRACK_LOAD, "Eleve até a linha dos ombros sem usar impulso do tronco."),
    ("landmine_press", "Landmine press", "Musculação", "Ombros", "Peitoral superior, tríceps, core", "Deltoide anterior", "Barra landmine", "Intermediário", TRACK_LOAD, "Empurre a barra em diagonal mantendo costelas e quadril controlados."),
    ("elevacao_lateral_halteres", "Elevação lateral com halteres", "Musculação", "Ombros", "Trapézio", "Deltoide lateral", "Halteres", "Iniciante", TRACK_LOAD, "Eleve os braços com cotovelos suaves e sem encolher os ombros."),
    ("elevacao_lateral_cabo", "Elevação lateral no cabo", "Musculação", "Ombros", "Trapézio", "Deltoide lateral", "Polia baixa", "Intermediário", TRACK_LOAD, "Mantenha tensão contínua e mova o braço sem inclinar o tronco."),
    ("elevacao_lateral_maquina", "Elevação lateral na máquina", "Musculação", "Ombros", "Trapézio", "Deltoide lateral", "Máquina", "Iniciante", TRACK_LOAD, "Ajuste o assento para alinhar o eixo da máquina aos ombros."),
    ("crucifixo_inverso", "Crucifixo inverso", "Musculação", "Ombros", "Romboides, trapézio médio", "Deltoide posterior", "Halteres, polia ou máquina", "Iniciante", TRACK_LOAD, "Abra os braços sem projetar a cabeça ou elevar os ombros."),
    ("face_pull", "Face pull", "Musculação", "Ombros", "Trapézio médio, romboides, manguito rotador", "Deltoide posterior", "Polia e corda", "Iniciante", TRACK_LOAD, "Puxe a corda em direção ao rosto mantendo cotovelos elevados e controle."),
    ("remada_alta", "Remada alta", "Musculação", "Ombros", "Trapézio, bíceps", "Deltoide lateral", "Barra, halteres ou polia", "Intermediário", TRACK_LOAD, "Use pegada confortável e não eleve os cotovelos além de uma amplitude segura."),

    # Braços e antebraços
    ("rosca_direta", "Rosca direta", "Musculação", "Bíceps", "Antebraços", "Bíceps braquial", "Barra", "Iniciante", TRACK_LOAD, "Mantenha os cotovelos próximos ao corpo e evite impulso."),
    ("rosca_alternada", "Rosca alternada", "Musculação", "Bíceps", "Antebraços", "Bíceps braquial", "Halteres", "Iniciante", TRACK_LOAD, "Alterne os braços sem girar ou balançar o tronco."),
    ("rosca_concentrada", "Rosca concentrada", "Musculação", "Bíceps", "Braquial", "Bíceps braquial", "Halter", "Iniciante", TRACK_LOAD, "Apoie o braço e complete o movimento sem retirar o cotovelo da posição."),
    ("rosca_scott", "Rosca Scott", "Musculação", "Bíceps", "Braquial", "Bíceps braquial", "Banco Scott e barra", "Intermediário", TRACK_LOAD, "Não estenda o cotovelo de forma brusca no final da descida."),
    ("rosca_inclinada", "Rosca inclinada", "Musculação", "Bíceps", "Braquial", "Cabeça longa do bíceps", "Halteres e banco inclinado", "Intermediário", TRACK_LOAD, "Mantenha os ombros apoiados e evite levar os cotovelos à frente."),
    ("rosca_cabo", "Rosca no cabo", "Musculação", "Bíceps", "Antebraços", "Bíceps braquial", "Polia baixa", "Iniciante", TRACK_LOAD, "Conserve os cotovelos estáveis e aproveite a tensão contínua da polia."),
    ("rosca_martelo", "Rosca martelo", "Musculação", "Bíceps", "Braquiorradial, antebraços", "Braquial", "Halteres ou corda", "Iniciante", TRACK_LOAD, "Mantenha as palmas voltadas uma para a outra e os punhos firmes."),
    ("triceps_pulley", "Tríceps pulley", "Musculação", "Tríceps", "Antebraços", "Cabeças lateral e medial", "Polia alta e barra", "Iniciante", TRACK_LOAD, "Estenda os cotovelos sem afastá-los do corpo ou inclinar o tronco."),
    ("triceps_corda", "Tríceps na corda", "Musculação", "Tríceps", "Antebraços", "Cabeças lateral e medial", "Polia alta e corda", "Iniciante", TRACK_LOAD, "Abra suavemente as pontas da corda ao finalizar a extensão."),
    ("triceps_frances", "Tríceps francês", "Musculação", "Tríceps", "Core", "Cabeça longa", "Halter, barra ou polia", "Intermediário", TRACK_LOAD, "Mantenha os cotovelos apontados à frente e evite compensar com a lombar."),
    ("triceps_testa", "Tríceps testa", "Musculação", "Tríceps", "Antebraços", "Cabeça longa", "Barra e banco", "Intermediário", TRACK_LOAD, "Controle a flexão dos cotovelos e mantenha os braços estáveis."),
    ("triceps_coice", "Tríceps coice", "Musculação", "Tríceps", "Ombro posterior", "Cabeça lateral", "Halter ou polia", "Iniciante", TRACK_LOAD, "Fixe o braço junto ao tronco e mova somente o antebraço."),
    ("supino_fechado", "Supino fechado", "Musculação", "Tríceps", "Peitoral, ombro anterior", "Tríceps completo", "Barra e banco", "Intermediário", TRACK_LOAD, "Use pegada confortável e mantenha os cotovelos próximos ao corpo."),
    ("paralelas_triceps", "Paralelas com foco no tríceps", "Calistenia", "Tríceps", "Peitoral, ombro anterior", "Tríceps completo", "Barras paralelas", "Avançado", TRACK_REPS, "Mantenha o tronco mais vertical e use somente amplitude confortável."),
    ("rosca_inversa", "Rosca inversa", "Musculação", "Antebraços", "Bíceps, braquial", "Extensores e braquiorradial", "Barra ou polia", "Intermediário", TRACK_LOAD, "Mantenha punhos neutros e cotovelos estáveis durante a subida."),
    ("flexao_punho", "Flexão de punho", "Musculação", "Antebraços", "", "Flexores do antebraço", "Halteres ou barra", "Iniciante", TRACK_LOAD, "Apoie os antebraços e mova somente os punhos com controle."),
    ("extensao_punho", "Extensão de punho", "Musculação", "Antebraços", "", "Extensores do antebraço", "Halteres ou barra", "Iniciante", TRACK_LOAD, "Use carga leve e evite retirar os antebraços do apoio."),
    ("suspensao_barra", "Suspensão na barra", "Calistenia", "Antebraços", "Costas, ombros, core", "Pegada", "Barra fixa", "Iniciante", TRACK_TIME, "Sustente o corpo com ombros ativos e interrompa se perder a pegada segura."),

    # Pernas e glúteos
    ("agachamento_livre", "Agachamento livre", "Musculação", "Quadríceps", "Glúteos, posteriores, adutores, core", "Quadríceps completo", "Barra e suporte", "Intermediário", TRACK_LOAD, "Mantenha o tronco firme, joelhos na direção dos pés e calcanhares apoiados."),
    ("leg_press", "Leg press", "Musculação", "Quadríceps", "Glúteos, posteriores", "Quadríceps completo", "Máquina leg press", "Iniciante", TRACK_LOAD, "Mantenha lombar apoiada e não trave os joelhos ao estender as pernas."),
    ("cadeira_extensora", "Cadeira extensora", "Musculação", "Quadríceps", "", "Quadríceps completo", "Máquina", "Iniciante", TRACK_LOAD, "Alinhe o joelho ao eixo da máquina e controle a descida."),
    ("avanco", "Avanço", "Musculação", "Quadríceps", "Glúteos, posteriores, adutores", "Quadríceps e glúteos", "Peso corporal ou halteres", "Intermediário", TRACK_LOAD, "Dê um passo estável e mantenha o joelho acompanhando a direção do pé."),
    ("passada", "Passada", "Funcional", "Quadríceps", "Glúteos, posteriores, panturrilhas", "Quadríceps e glúteos", "Peso corporal ou halteres", "Intermediário", TRACK_LOAD, "Caminhe com passos controlados e tronco estável."),
    ("agachamento_bulgaro", "Agachamento búlgaro", "Musculação", "Quadríceps", "Glúteos, posteriores, adutores", "Quadríceps e glúteos", "Banco e halteres", "Intermediário", TRACK_LOAD, "Ajuste a distância do pé dianteiro para manter equilíbrio e alinhamento."),
    ("step_up", "Step-up", "Funcional", "Quadríceps", "Glúteos, posteriores, panturrilhas", "Quadríceps e glúteos", "Caixa ou banco", "Iniciante", TRACK_LOAD, "Suba empurrando pela perna apoiada sem impulsionar excessivamente a perna de trás."),
    ("levantamento_terra_romeno", "Levantamento terra romeno", "Musculação", "Posterior de coxa", "Glúteos, lombar, antebraços", "Isquiotibiais", "Barra ou halteres", "Intermediário", TRACK_LOAD, "Leve o quadril para trás mantendo a coluna neutra e joelhos suaves."),
    ("stiff", "Stiff", "Musculação", "Posterior de coxa", "Glúteos, lombar", "Isquiotibiais", "Barra ou halteres", "Intermediário", TRACK_LOAD, "Desça a carga junto às pernas sem perder a posição neutra da coluna."),
    ("mesa_flexora", "Mesa flexora", "Musculação", "Posterior de coxa", "Panturrilhas", "Isquiotibiais", "Máquina", "Iniciante", TRACK_LOAD, "Mantenha o quadril apoiado e controle tanto a flexão quanto o retorno."),
    ("cadeira_flexora", "Cadeira flexora", "Musculação", "Posterior de coxa", "Panturrilhas", "Isquiotibiais", "Máquina", "Iniciante", TRACK_LOAD, "Ajuste os apoios e evite retirar o quadril do assento."),
    ("flexao_nordica", "Flexão nórdica", "Calistenia", "Posterior de coxa", "Glúteos, core", "Isquiotibiais", "Apoio para tornozelos", "Avançado", TRACK_REPS, "Desça lentamente com o corpo alinhado e use assistência quando necessário."),
    ("good_morning", "Good morning", "Musculação", "Posterior de coxa", "Glúteos, lombar, core", "Isquiotibiais", "Barra", "Avançado", TRACK_LOAD, "Faça a dobradiça de quadril com carga moderada e coluna neutra."),
    ("elevacao_pelvica", "Elevação pélvica", "Musculação", "Glúteos", "Posterior de coxa, core", "Glúteo máximo", "Barra e banco", "Iniciante", TRACK_LOAD, "Eleve o quadril sem hiperestender a lombar e mantenha os pés firmes."),
    ("ponte_gluteos", "Ponte de glúteos", "Calistenia", "Glúteos", "Posterior de coxa, core", "Glúteo máximo", "Peso corporal", "Iniciante", TRACK_REPS, "Contraia os glúteos no topo mantendo costelas e pelve controladas."),
    ("levantamento_terra_sumo", "Levantamento terra sumô", "Musculação", "Glúteos", "Adutores, quadríceps, posteriores, lombar", "Glúteo máximo", "Barra", "Intermediário", TRACK_LOAD, "Use base ampla, joelhos na direção dos pés e mantenha a barra próxima."),
    ("coice_cabo", "Coice no cabo", "Musculação", "Glúteos", "Posterior de coxa", "Glúteo máximo", "Polia baixa", "Iniciante", TRACK_LOAD, "Estenda o quadril sem girar a pelve ou arquear a lombar."),
    ("cadeira_abdutora", "Cadeira abdutora", "Musculação", "Abdutores", "Glúteos", "Glúteo médio e mínimo", "Máquina", "Iniciante", TRACK_LOAD, "Abra as pernas com controle mantendo quadril e tronco apoiados."),
    ("caminhada_lateral_elastico", "Caminhada lateral com elástico", "Funcional", "Abdutores", "Glúteos, quadríceps", "Glúteo médio", "Miniband", "Iniciante", TRACK_REPS, "Mantenha tensão no elástico, joelhos alinhados e passos curtos."),
    ("abducao_cabo", "Abdução no cabo", "Musculação", "Abdutores", "Glúteos", "Glúteo médio e mínimo", "Polia baixa", "Intermediário", TRACK_LOAD, "Afaste a perna sem inclinar o tronco ou girar o quadril."),
    ("abducao_lateral_deitado", "Abdução lateral deitado", "Calistenia", "Abdutores", "Glúteos", "Glúteo médio", "Peso corporal ou miniband", "Iniciante", TRACK_REPS, "Mantenha a pelve empilhada e eleve a perna sem girar o pé para cima."),
    ("cadeira_adutora", "Cadeira adutora", "Musculação", "Adutores", "", "Parte interna da coxa", "Máquina", "Iniciante", TRACK_LOAD, "Aproxime as pernas de forma controlada sem usar impulso."),
    ("agachamento_sumo", "Agachamento sumô", "Musculação", "Adutores", "Glúteos, quadríceps, core", "Parte interna da coxa", "Halter, kettlebell ou barra", "Iniciante", TRACK_LOAD, "Use base ampla e mantenha joelhos acompanhando a direção dos pés."),
    ("afundo_lateral", "Afundo lateral", "Funcional", "Adutores", "Glúteos, quadríceps", "Parte interna da coxa", "Peso corporal ou halteres", "Intermediário", TRACK_LOAD, "Desloque o quadril para trás na perna flexionada e mantenha a outra estendida."),
    ("aducao_cabo", "Adução no cabo", "Musculação", "Adutores", "Core", "Parte interna da coxa", "Polia baixa", "Intermediário", TRACK_LOAD, "Cruze a perna de forma controlada sem girar a pelve."),
    ("panturrilha_em_pe", "Panturrilha em pé", "Musculação", "Panturrilhas", "", "Gastrocnêmio", "Máquina, barra ou halteres", "Iniciante", TRACK_LOAD, "Eleve os calcanhares com joelhos estendidos e controle a descida."),
    ("panturrilha_sentada", "Panturrilha sentada", "Musculação", "Panturrilhas", "", "Sóleo", "Máquina ou halter", "Iniciante", TRACK_LOAD, "Mantenha os joelhos flexionados e use amplitude controlada."),
    ("panturrilha_leg_press", "Panturrilha no leg press", "Musculação", "Panturrilhas", "", "Gastrocnêmio", "Máquina leg press", "Iniciante", TRACK_LOAD, "Movimente somente os tornozelos e mantenha os joelhos estáveis."),
    ("panturrilha_unilateral", "Panturrilha unilateral", "Musculação", "Panturrilhas", "Core", "Gastrocnêmio e sóleo", "Degrau ou máquina", "Intermediário", TRACK_LOAD, "Use apoio para equilíbrio e complete a mesma amplitude dos dois lados."),
    ("panturrilha_degrau", "Panturrilha no degrau", "Calistenia", "Panturrilhas", "", "Gastrocnêmio", "Degrau", "Iniciante", TRACK_REPS, "Desça o calcanhar com controle e suba sem impulsionar o corpo."),
    ("elevacao_ponta_pes", "Elevação da ponta dos pés", "Calistenia", "Tibial anterior", "", "Parte frontal da canela", "Parede ou peso corporal", "Iniciante", TRACK_REPS, "Mantenha os calcanhares apoiados e eleve o antepé de forma controlada."),
    ("dorsiflexao_elastico", "Dorsiflexão com elástico", "Mobilidade", "Tibial anterior", "", "Parte frontal da canela", "Elástico", "Iniciante", TRACK_REPS, "Puxe a ponta do pé em direção à canela sem mover o joelho."),

    # Abdômen, core e lombar
    ("abdominal_tradicional", "Abdominal tradicional", "Calistenia", "Abdômen", "Flexores do quadril", "Reto abdominal", "Peso corporal", "Iniciante", TRACK_REPS, "Eleve o tronco sem puxar o pescoço e mantenha a lombar controlada."),
    ("crunch_cabo", "Crunch no cabo", "Musculação", "Abdômen", "", "Reto abdominal", "Polia alta", "Intermediário", TRACK_LOAD, "Flexione o tronco pelo abdômen sem transformar o movimento em agachamento."),
    ("abdominal_maquina", "Abdominal na máquina", "Musculação", "Abdômen", "Flexores do quadril", "Reto abdominal", "Máquina", "Iniciante", TRACK_LOAD, "Ajuste o equipamento e flexione o tronco com movimento controlado."),
    ("abdominal_reverso", "Abdominal reverso", "Calistenia", "Abdômen", "Flexores do quadril", "Reto abdominal inferior", "Peso corporal", "Iniciante", TRACK_REPS, "Eleve a pelve suavemente sem usar balanço das pernas."),
    ("elevacao_pernas", "Elevação de pernas", "Calistenia", "Abdômen", "Flexores do quadril, core", "Abdômen inferior", "Peso corporal", "Intermediário", TRACK_REPS, "Mantenha a lombar controlada e desça as pernas somente até preservar a postura."),
    ("elevacao_joelhos_barra", "Elevação de joelhos na barra", "Calistenia", "Abdômen", "Flexores do quadril, antebraços, costas", "Abdômen inferior", "Barra fixa", "Intermediário", TRACK_REPS, "Evite balançar e aproxime os joelhos usando o abdômen."),
    ("abdominal_canivete", "Abdominal canivete", "Calistenia", "Abdômen", "Flexores do quadril, core", "Reto abdominal", "Peso corporal", "Intermediário", TRACK_REPS, "Aproxime tronco e pernas com controle sem usar impulso."),
    ("prancha_lateral", "Prancha lateral", "Calistenia", "Oblíquos", "Glúteos, ombros, core", "Oblíquos", "Peso corporal", "Iniciante", TRACK_TIME, "Mantenha cabeça, tronco e quadril alinhados durante todo o tempo."),
    ("wood_chop", "Wood chop", "Funcional", "Oblíquos", "Ombros, core, glúteos", "Oblíquos", "Polia ou elástico", "Intermediário", TRACK_LOAD, "Gire o tronco de forma coordenada sem forçar a lombar."),
    ("abdominal_bicicleta", "Abdominal bicicleta", "Calistenia", "Oblíquos", "Reto abdominal, flexores do quadril", "Oblíquos", "Peso corporal", "Iniciante", TRACK_REPS, "Alterne os lados lentamente sem puxar o pescoço."),
    ("rotacao_cabo", "Rotação no cabo", "Musculação", "Oblíquos", "Core, ombros", "Oblíquos", "Polia", "Intermediário", TRACK_LOAD, "Mantenha quadril estável e gire sem movimentos bruscos."),
    ("prancha_frontal", "Prancha frontal", "Calistenia", "Core", "Abdômen, ombros, glúteos", "Core profundo", "Peso corporal", "Iniciante", TRACK_TIME, "Mantenha o corpo alinhado, abdômen firme e respiração contínua."),
    ("dead_bug", "Dead bug", "Funcional", "Core", "Abdômen, flexores do quadril", "Core profundo", "Peso corporal", "Iniciante", TRACK_REPS, "Mantenha a lombar apoiada enquanto alterna braços e pernas."),
    ("bird_dog", "Bird dog", "Funcional", "Core", "Glúteos, lombar, ombros", "Core profundo", "Peso corporal", "Iniciante", TRACK_REPS, "Estenda braço e perna opostos sem girar a pelve."),
    ("hollow_hold", "Hollow hold", "Calistenia", "Core", "Abdômen, flexores do quadril", "Core profundo", "Peso corporal", "Intermediário", TRACK_TIME, "Mantenha a lombar apoiada e ajuste braços ou pernas para preservar a posição."),
    ("pallof_press", "Pallof press", "Funcional", "Core", "Oblíquos, glúteos", "Antirrotação", "Polia ou elástico", "Intermediário", TRACK_LOAD, "Resista à rotação enquanto estende os braços à frente do corpo."),
    ("extensao_lombar", "Extensão lombar", "Musculação", "Lombar", "Glúteos, posteriores", "Eretores da coluna", "Banco romano", "Intermediário", TRACK_LOAD, "Eleve o tronco apenas até alinhar o corpo, sem hiperestender a coluna."),
    ("superman", "Superman", "Calistenia", "Lombar", "Glúteos, ombros", "Eretores da coluna", "Peso corporal", "Iniciante", TRACK_TIME, "Eleve braços e pernas suavemente sem buscar amplitude excessiva."),

    # Cardio e corpo inteiro
    ("caminhada", "Caminhada", "Cardio", "Cardiorrespiratório", "Quadríceps, glúteos, posteriores, panturrilhas", "Corpo inteiro", "Esteira ou área livre", "Iniciante", TRACK_CARDIO, "Mantenha passada confortável e intensidade compatível com seu condicionamento."),
    ("corrida", "Corrida", "Cardio", "Cardiorrespiratório", "Quadríceps, glúteos, posteriores, panturrilhas, core", "Corpo inteiro", "Esteira ou área livre", "Intermediário", TRACK_CARDIO, "Aumente volume e velocidade progressivamente, mantendo passada controlada."),
    ("bicicleta", "Bicicleta", "Cardio", "Cardiorrespiratório", "Quadríceps, glúteos, posteriores, panturrilhas", "Membros inferiores", "Bicicleta ou ergométrica", "Iniciante", TRACK_CARDIO, "Ajuste o banco e mantenha cadência adequada à intensidade planejada."),
    ("escada", "Escada", "Cardio", "Cardiorrespiratório", "Glúteos, quadríceps, panturrilhas", "Membros inferiores", "Escada ou simulador", "Intermediário", TRACK_CARDIO, "Suba com postura estável e evite apoiar todo o peso nos braços."),
    ("remo_ergometro", "Remo ergométrico", "Cardio", "Cardiorrespiratório", "Costas, quadríceps, glúteos, bíceps, core", "Corpo inteiro", "Remo ergométrico", "Intermediário", TRACK_CARDIO, "Inicie pelas pernas, complete com o tronco e finalize puxando com os braços."),
    ("corda", "Pular corda", "Cardio", "Cardiorrespiratório", "Panturrilhas, ombros, antebraços, core", "Corpo inteiro", "Corda", "Intermediário", TRACK_CARDIO, "Faça saltos baixos, aterrisse suavemente e gire a corda pelos punhos."),
    ("kettlebell_swing", "Kettlebell swing", "Funcional", "Corpo inteiro", "Glúteos, posteriores, lombar, ombros, core", "Cadeia posterior", "Kettlebell", "Intermediário", TRACK_LOAD, "Gere força pelo quadril; os braços apenas conduzem o kettlebell."),
    ("burpee", "Burpee", "Funcional", "Corpo inteiro", "Peitoral, ombros, tríceps, pernas, core", "Corpo inteiro", "Peso corporal", "Intermediário", TRACK_REPS, "Mantenha controle na prancha e aterrisse com joelhos alinhados."),
    ("mountain_climber", "Mountain climber", "Funcional", "Corpo inteiro", "Core, ombros, quadríceps, flexores do quadril", "Corpo inteiro", "Peso corporal", "Iniciante", TRACK_TIME, "Mantenha ombros sobre as mãos e alterne os joelhos sem elevar o quadril."),

    # Mobilidade
    ("mobilidade_tornozelo", "Mobilidade de tornozelo", "Mobilidade", "Mobilidade", "Panturrilhas, tibial anterior", "Tornozelo", "Parede ou apoio", "Iniciante", TRACK_MOBILITY, "Leve o joelho à frente sem retirar o calcanhar do chão."),
    ("mobilidade_quadril_90_90", "Mobilidade de quadril 90/90", "Mobilidade", "Mobilidade", "Glúteos, adutores, abdutores", "Quadril", "Peso corporal", "Iniciante", TRACK_MOBILITY, "Alterne os lados lentamente e trabalhe somente em amplitude confortável."),
    ("rotacao_toracica", "Rotação torácica", "Mobilidade", "Mobilidade", "Costas, core, ombros", "Coluna torácica", "Peso corporal", "Iniciante", TRACK_MOBILITY, "Gire a parte alta das costas mantendo quadril e lombar controlados."),
    ("mobilidade_ombros_bastao", "Mobilidade de ombros com bastão", "Mobilidade", "Mobilidade", "Ombros, peitoral, costas", "Ombros", "Bastão ou elástico", "Iniciante", TRACK_MOBILITY, "Use pegada ampla e atravesse somente a amplitude sem dor."),
    ("alongamento_dinamico_posterior", "Alongamento dinâmico de posterior", "Mobilidade", "Mobilidade", "Posteriores, glúteos, panturrilhas", "Posterior de coxa", "Peso corporal", "Iniciante", TRACK_MOBILITY, "Alterne extensão e relaxamento sem movimentos bruscos ou dor."),
    ("alongamento_flexor_quadril", "Alongamento do flexor do quadril", "Mobilidade", "Mobilidade", "Quadríceps, glúteos", "Quadril", "Peso corporal", "Iniciante", TRACK_MOBILITY, "Mantenha a pelve neutra e avance suavemente sem arquear a lombar."),
)


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.is_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if request.endpoint in {
        "login", "register", "verify_email", "resend_verification",
        "forgot_password", "reset_password", "resend_password_reset",
    }:
        response.headers["Cache-Control"] = "no-store"
    return response


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def password_requirements(password: str, name: str = "", email: str = "") -> dict[str, bool]:
    lowered = password.casefold()
    obvious = ("123456", "abcdef", "qwerty", "senha", "password", "admin", "titan")
    personal_parts = [part.casefold() for part in re.findall(r"[\wÀ-ÿ]+", name) if len(part) >= 3]
    email_name = email.split("@", 1)[0].casefold()
    if len(email_name) >= 3:
        personal_parts.append(email_name)
    return {
        "length": len(password) >= 10,
        "lower": any(char.islower() for char in password),
        "upper": any(char.isupper() for char in password),
        "number": any(char.isdigit() for char in password),
        "symbol": any(not char.isalnum() and not char.isspace() for char in password),
        "not_obvious": (
            not any(fragment in lowered for fragment in obvious)
            and not any(part in lowered for part in personal_parts)
            and len(set(lowered)) >= 6
        ),
    }


def password_errors(password: str, name: str = "", email: str = "") -> list[str]:
    checks = password_requirements(password, name, email)
    labels = {
        "length": "pelo menos 10 caracteres",
        "lower": "uma letra minúscula",
        "upper": "uma letra maiúscula",
        "number": "um número",
        "symbol": "um símbolo",
        "not_obvious": "não usar nome, e-mail ou sequência óbvia",
    }
    return [labels[key] for key, passed in checks.items() if not passed]


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    if len(local) <= 2:
        visible = local[:1] + "*" * max(1, len(local) - 1)
    else:
        visible = local[0] + "*" * (len(local) - 2) + local[-1]
    return f"{visible}@{domain}"


def safe_next_url(value: str | None) -> str | None:
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return None


def verification_code_hash(user_id: int, code: str) -> str:
    key = str(app.secret_key).encode("utf-8")
    payload = f"{user_id}:{code}".encode("utf-8")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def password_reset_code_hash(user_id: int, code: str) -> str:
    key = str(app.secret_key).encode("utf-8")
    payload = f"password-reset:{user_id}:{code}".encode("utf-8")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def issue_verification_code(db: sqlite3.Connection, user_id: int) -> str:
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = utc_now()
    expires = now + timedelta(minutes=VERIFICATION_TTL_MINUTES)
    db.execute(
        """INSERT INTO email_verifications(user_id,code_hash,expires_at,attempts,last_sent_at)
           VALUES(?,?,?,?,?)
           ON CONFLICT(user_id) DO UPDATE SET code_hash=excluded.code_hash,
           expires_at=excluded.expires_at,attempts=0,last_sent_at=excluded.last_sent_at""",
        (user_id, verification_code_hash(user_id, code), expires.isoformat(), 0, now.isoformat()),
    )
    return code


def issue_password_reset_code(db: sqlite3.Connection, user_id: int) -> str:
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = utc_now()
    expires = now + timedelta(minutes=VERIFICATION_TTL_MINUTES)
    db.execute(
        """INSERT INTO password_resets(user_id,code_hash,expires_at,attempts,last_sent_at)
           VALUES(?,?,?,?,?)
           ON CONFLICT(user_id) DO UPDATE SET code_hash=excluded.code_hash,
           expires_at=excluded.expires_at,attempts=0,last_sent_at=excluded.last_sent_at""",
        (user_id, password_reset_code_hash(user_id, code), expires.isoformat(), 0, now.isoformat()),
    )
    return code


def email_contents(name: str, code: str, purpose: str = "verify") -> tuple[str, str, str]:
    is_reset = purpose == "reset"
    subject = (
        f"{code} é seu código para redefinir a senha TITAN"
        if is_reset else f"{code} é seu código de confirmação TITAN"
    )
    if is_reset:
        text_content = (
            f"Olá, {name}!\n\nSeu código para redefinir a senha do Projeto TITAN é: {code}\n\n"
            f"Ele expira em {VERIFICATION_TTL_MINUTES} minutos. Se você não solicitou a troca, ignore este e-mail."
        )
        email_title = "Redefina sua senha"
        email_intro = "Use o código abaixo para criar uma nova senha com segurança:"
        email_warning = "Se você não solicitou a troca de senha, ignore esta mensagem e sua senha continuará igual."
    else:
        text_content = (
            f"Olá, {name}!\n\nSeu código de confirmação do Projeto TITAN é: {code}\n\n"
            f"Ele expira em {VERIFICATION_TTL_MINUTES} minutos. Se você não criou esta conta, ignore este e-mail."
        )
        email_title = "Confirme seu e-mail"
        email_intro = "Use o código abaixo para liberar sua conta:"
        email_warning = "Se você não criou esta conta, ignore esta mensagem."
    safe_name = html_escape(name)
    html_content = f"""<!doctype html><html><body style="margin:0;background:#0b0f15;color:#eef2f6;font-family:Arial,sans-serif">
        <div style="max-width:520px;margin:0 auto;padding:36px 22px">
          <div style="color:#f39a2f;font-size:28px;font-weight:900;letter-spacing:2px">TITAN</div>
          <div style="margin-top:22px;padding:28px;background:#151d27;border:1px solid #303b47;border-radius:16px">
            <h1 style="margin:0 0 12px;font-size:23px">{email_title}</h1>
            <p style="color:#a8b3bf;line-height:1.6">Olá, {safe_name}! {email_intro}</p>
            <div style="margin:22px 0;padding:16px;text-align:center;background:#0c1219;border-radius:12px;color:#ffad4c;font-size:34px;font-weight:900;letter-spacing:9px">{code}</div>
            <p style="color:#a8b3bf;font-size:13px">O código expira em {VERIFICATION_TTL_MINUTES} minutos. {email_warning}</p>
          </div>
        </div></body></html>"""
    return subject, text_content, html_content


def send_brevo_api(
    name: str, email: str, subject: str, text_content: str, html_content: str
) -> bool:
    api_key = os.getenv("BREVO_API_KEY", "").strip()
    sender_email = os.getenv("BREVO_SENDER_EMAIL", "").strip()
    sender_name = os.getenv("BREVO_SENDER_NAME", "Projeto TITAN").strip()
    if not api_key or not sender_email:
        app.logger.error(
            "Configuração Brevo incompleta: informe BREVO_API_KEY e BREVO_SENDER_EMAIL."
        )
        return False

    payload = json.dumps(
        {
            "sender": {"name": sender_name, "email": sender_email},
            "to": [{"name": name, "email": email}],
            "subject": subject,
            "textContent": text_content,
            "htmlContent": html_content,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    api_request = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/email",
        data=payload,
        method="POST",
        headers={
            "accept": "application/json",
            "api-key": api_key,
            "content-type": "application/json",
            "user-agent": "Projeto-TITAN/3.8",
        },
    )
    try:
        with urllib.request.urlopen(api_request, timeout=15) as response:
            return 200 <= response.status < 300
    except urllib.error.HTTPError as error:
        app.logger.error("A Brevo recusou o envio do e-mail (HTTP %s).", error.code)
    except (urllib.error.URLError, OSError):
        app.logger.exception("Não foi possível conectar à API da Brevo.")
    return False


def send_smtp(
    email: str, subject: str, text_content: str, html_content: str
) -> bool:
    host = os.getenv("SMTP_HOST", "").strip()
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    from_email = os.getenv("SMTP_FROM_EMAIL", username).strip()
    from_name = os.getenv("SMTP_FROM_NAME", "Projeto TITAN").strip()
    use_ssl = os.getenv("SMTP_USE_SSL", "false").lower() == "true"
    use_tls = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
    default_port = 465 if use_ssl else 587
    try:
        port = int(os.getenv("SMTP_PORT", str(default_port)))
    except ValueError:
        app.logger.error("SMTP_PORT precisa ser um número.")
        return False
    if not host or not from_email:
        app.logger.error("Configuração SMTP incompleta: informe SMTP_HOST e SMTP_FROM_EMAIL.")
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{from_name} <{from_email}>"
    message["To"] = email
    message.set_content(text_content)
    message.add_alternative(html_content, subtype="html")

    context = ssl.create_default_context()
    try:
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=15, context=context) as smtp:
                if username:
                    smtp.login(username, password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=15) as smtp:
                smtp.ehlo()
                if use_tls:
                    smtp.starttls(context=context)
                    smtp.ehlo()
                if username:
                    smtp.login(username, password)
                smtp.send_message(message)
        return True
    except (OSError, smtplib.SMTPException):
        app.logger.exception("Não foi possível enviar o e-mail por SMTP.")
        return False


def _send_verification_email(name: str, email: str, code: str, purpose: str = "verify") -> bool:
    is_reset = purpose == "reset"
    configured_mode = os.getenv("MAIL_MODE", "").strip().lower()
    if configured_mode:
        mode = configured_mode
    elif os.getenv("BREVO_API_KEY"):
        mode = "brevo_api"
    else:
        mode = "smtp" if os.getenv("RAILWAY_ENVIRONMENT") else "console"

    if mode == "console":
        label = "recuperação de senha" if is_reset else "confirmação de e-mail"
        app.logger.warning("TITAN DEV — código de %s para %s: %s", label, email, code)
        return True

    subject, text_content, html_content = email_contents(name, code, purpose)
    if mode in {"brevo", "brevo_api", "api"}:
        return send_brevo_api(name, email, subject, text_content, html_content)
    if mode == "smtp":
        return send_smtp(email, subject, text_content, html_content)

    app.logger.error("MAIL_MODE inválido. Use 'brevo_api', 'smtp' ou 'console'.")
    return False


def send_verification_email(name: str, email: str, code: str) -> bool:
    """Fronteira segura: nenhuma falha do provedor de e-mail derruba o cadastro."""
    try:
        return _send_verification_email(name, email, code)
    except Exception:
        app.logger.exception("Falha inesperada ao preparar ou enviar o e-mail de confirmação.")
        return False


def send_password_reset_email(name: str, email: str, code: str) -> bool:
    """O reset de senha também nunca pode derrubar a aplicação por falha do provedor."""
    try:
        return _send_verification_email(name, email, code, purpose="reset")
    except Exception:
        app.logger.exception("Falha inesperada ao preparar ou enviar o e-mail de recuperação.")
        return False


def db_conn() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=20)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    return connection


def sync_exercise_catalog(db: sqlite3.Connection) -> None:
    """Mantém o catálogo oficial atualizado sem tocar em imagens ou vídeos."""
    db.execute("UPDATE exercise_catalog SET active=0")
    statement = """
        INSERT INTO exercise_catalog(
            catalog_key,name,modality,primary_group,secondary_muscles,
            muscle_region,equipment,difficulty,tracking_method,description,
            active,sort_order
        ) VALUES(?,?,?,?,?,?,?,?,?,?,1,?)
        ON CONFLICT(catalog_key) DO UPDATE SET
            name=excluded.name,
            modality=excluded.modality,
            primary_group=excluded.primary_group,
            secondary_muscles=excluded.secondary_muscles,
            muscle_region=excluded.muscle_region,
            equipment=excluded.equipment,
            difficulty=excluded.difficulty,
            tracking_method=excluded.tracking_method,
            description=excluded.description,
            active=1,
            sort_order=excluded.sort_order
    """
    for sort_order, exercise in enumerate(EXERCISE_CATALOG, start=1):
        db.execute(statement, (*exercise, sort_order))


def init_db() -> None:
    with db_conn() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            email_verified INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS email_verifications(
            user_id INTEGER PRIMARY KEY,
            code_hash TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_sent_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS password_resets(
            user_id INTEGER PRIMARY KEY,
            code_hash TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_sent_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS settings(
            user_id INTEGER PRIMARY KEY,
            age INTEGER DEFAULT 27,
            height REAL DEFAULT 1.80,
            start_weight REAL DEFAULT 65,
            goal_weight REAL DEFAULT 70,
            final_goal REAL DEFAULT 85,
            calories INTEGER DEFAULT 2800,
            protein INTEGER DEFAULT 130,
            carbs INTEGER DEFAULT 380,
            fat INTEGER DEFAULT 85,
            water REAL DEFAULT 2.5,
            weekly_target REAL DEFAULT 0.30,
            sex TEXT DEFAULT 'male',
            activity_level TEXT DEFAULT 'sedentary',
            goal_type TEXT DEFAULT 'gain',
            training_days INTEGER DEFAULT 0,
            appetite_level TEXT DEFAULT 'low',
            meals_per_day INTEGER DEFAULT 4,
            budget_monthly REAL DEFAULT 0,
            restrictions TEXT DEFAULT '',
            bmr REAL DEFAULT 0,
            tdee REAL DEFAULT 0,
            onboarding_completed INTEGER DEFAULT 0,
            calculation_version TEXT DEFAULT 'TITAN-1.0',
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS foods(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            serving REAL NOT NULL DEFAULT 100,
            unit TEXT NOT NULL DEFAULT 'g',
            calories REAL NOT NULL,
            protein REAL NOT NULL DEFAULT 0,
            carbs REAL NOT NULL DEFAULT 0,
            fat REAL NOT NULL DEFAULT 0,
            fiber REAL NOT NULL DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS meals(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            meal_type TEXT NOT NULL DEFAULT 'Refeição',
            food_id INTEGER NOT NULL,
            quantity REAL NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(food_id) REFERENCES foods(id) ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS weights(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            weight REAL NOT NULL,
            UNIQUE(user_id, day),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS measurements(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            arm REAL, chest REAL, waist REAL, abdomen REAL,
            hip REAL, thigh REAL, calf REAL, shoulders REAL,
            notes TEXT DEFAULT '',
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS habits(
            user_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            water REAL DEFAULT 0,
            sleep REAL DEFAULT 0,
            trained INTEGER DEFAULT 0,
            appetite INTEGER DEFAULT 0,
            PRIMARY KEY(user_id, day),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS photos(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            angle TEXT NOT NULL,
            filename TEXT NOT NULL,
            notes TEXT DEFAULT '',
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS exercises(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            muscle TEXT NOT NULL DEFAULT '',
            description TEXT DEFAULT '',
            image_filename TEXT DEFAULT '',
            video_url TEXT DEFAULT '',
            catalog_key TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS exercise_catalog(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            catalog_key TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            modality TEXT NOT NULL,
            primary_group TEXT NOT NULL,
            secondary_muscles TEXT NOT NULL DEFAULT '',
            muscle_region TEXT NOT NULL DEFAULT '',
            equipment TEXT NOT NULL DEFAULT '',
            difficulty TEXT NOT NULL DEFAULT 'Iniciante',
            tracking_method TEXT NOT NULL DEFAULT 'Séries, repetições e carga',
            description TEXT NOT NULL DEFAULT '',
            image_filename TEXT NOT NULL DEFAULT '',
            video_url TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_exercise_catalog_group
            ON exercise_catalog(primary_group,sort_order);
        CREATE INDEX IF NOT EXISTS idx_exercise_catalog_modality
            ON exercise_catalog(modality,sort_order);

        CREATE TABLE IF NOT EXISTS workouts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            exercise_id INTEGER NOT NULL,
            sets INTEGER DEFAULT 3,
            reps INTEGER DEFAULT 10,
            load REAL DEFAULT 0,
            notes TEXT DEFAULT '',
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(exercise_id) REFERENCES exercises(id) ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS plan_settings(
            user_id INTEGER PRIMARY KEY,
            days INTEGER DEFAULT 30,
            meals_per_day INTEGER DEFAULT 2,
            completed_days INTEGER DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS plan_items(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            food_id INTEGER,
            name TEXT NOT NULL,
            unit TEXT NOT NULL DEFAULT 'g',
            daily_qty REAL NOT NULL DEFAULT 0,
            package_qty REAL NOT NULL DEFAULT 1,
            package_price REAL NOT NULL DEFAULT 0,
            category TEXT NOT NULL DEFAULT 'Marmitas',
            current_stock REAL NOT NULL DEFAULT 0,
            notes TEXT DEFAULT '',
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(food_id) REFERENCES foods(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS stores(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS store_prices(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            store_id INTEGER NOT NULL,
            plan_item_id INTEGER NOT NULL,
            package_price REAL NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, store_id, plan_item_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(store_id) REFERENCES stores(id) ON DELETE CASCADE,
            FOREIGN KEY(plan_item_id) REFERENCES plan_items(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS calendar_meals(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            time TEXT NOT NULL,
            title TEXT NOT NULL,
            food_id INTEGER,
            quantity REAL DEFAULT 0,
            notes TEXT DEFAULT '',
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(food_id) REFERENCES foods(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS reminders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            time TEXT NOT NULL,
            days TEXT NOT NULL DEFAULT 'Todos os dias',
            enabled INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        """)

        # Migração compatível com bancos das versões anteriores.
        existing = {row["name"] for row in db.execute("PRAGMA table_info(settings)").fetchall()}
        additions = {
            "sex": "TEXT DEFAULT 'male'",
            "activity_level": "TEXT DEFAULT 'sedentary'",
            "goal_type": "TEXT DEFAULT 'gain'",
            "training_days": "INTEGER DEFAULT 0",
            "appetite_level": "TEXT DEFAULT 'low'",
            "meals_per_day": "INTEGER DEFAULT 4",
            "budget_monthly": "REAL DEFAULT 0",
            "restrictions": "TEXT DEFAULT ''",
            "bmr": "REAL DEFAULT 0",
            "tdee": "REAL DEFAULT 0",
            "onboarding_completed": "INTEGER DEFAULT 0",
            "calculation_version": "TEXT DEFAULT 'TITAN-1.0'",
        }
        for column, definition in additions.items():
            if column not in existing:
                db.execute(f"ALTER TABLE settings ADD COLUMN {column} {definition}")

        # Contas criadas antes da verificação por e-mail permanecem válidas.
        user_columns = {row["name"] for row in db.execute("PRAGMA table_info(users)").fetchall()}
        if "email_verified" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 1")

        # Migração V3.7: mantém todos os alimentos e apenas acrescenta favoritos.
        food_columns = {row["name"] for row in db.execute("PRAGMA table_info(foods)").fetchall()}
        if "favorite" not in food_columns:
            db.execute("ALTER TABLE foods ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0")

        # Migração V3.10: catálogo global classificado, sem duplicar os mesmos
        # exercícios para cada conta e sem alterar treinos já registrados.
        exercise_columns = {row["name"] for row in db.execute("PRAGMA table_info(exercises)").fetchall()}
        if "catalog_key" not in exercise_columns:
            db.execute("ALTER TABLE exercises ADD COLUMN catalog_key TEXT NOT NULL DEFAULT ''")
        db.execute("CREATE INDEX IF NOT EXISTS idx_exercises_catalog_key ON exercises(catalog_key)")
        sync_exercise_catalog(db)
        db.execute("""
            UPDATE exercises
               SET catalog_key=(
                   SELECT catalog_key FROM exercise_catalog
                    WHERE lower(exercise_catalog.name)=lower(exercises.name)
                    LIMIT 1
               )
             WHERE catalog_key=''
               AND EXISTS(
                   SELECT 1 FROM exercise_catalog
                    WHERE lower(exercise_catalog.name)=lower(exercises.name)
               )
        """)
        db.execute("""
            UPDATE exercises SET catalog_key='desenvolvimento_militar'
             WHERE catalog_key='' AND lower(trim(name))='desenvolvimento'
        """)


init_db()


def today() -> str:
    return date.today().isoformat()


def tracking_day(value: str | None) -> str:
    try:
        return date.fromisoformat(value or "").isoformat()
    except ValueError:
        return today()


def current_user_id() -> int:
    return int(session["user_id"])


def remember_undo(kind: str, payload: dict, message: str, return_to: str = "dashboard") -> None:
    """Guarda somente a última ação reversível na sessão assinada do usuário."""
    session["last_undo"] = {
        "kind": kind,
        "payload": payload,
        "user_id": current_user_id(),
        "created_at": int(utc_now().timestamp()),
        "return_to": return_to if return_to in {"dashboard", "foods"} else "dashboard",
    }
    flash(message, "undo")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.user:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@app.post("/desfazer")
@login_required
def undo_last_action():
    undo = session.pop("last_undo", None)
    if not undo or undo.get("user_id") != current_user_id():
        flash("Não há nenhuma ação recente para desfazer.")
        return redirect(url_for("dashboard"))
    if int(utc_now().timestamp()) - int(undo.get("created_at", 0)) > UNDO_TTL_SECONDS:
        flash("O tempo para desfazer essa ação terminou.")
        return redirect(url_for("dashboard"))

    kind = undo.get("kind")
    payload = undo.get("payload") or {}
    day = tracking_day(payload.get("day"))
    restored = False
    try:
        with db_conn() as db:
            if kind == "meal_add":
                db.execute(
                    "DELETE FROM meals WHERE id=? AND user_id=?",
                    (int(payload["id"]), current_user_id()),
                )
                restored = True
            elif kind == "repeat_meals":
                for meal_id in payload.get("ids", [])[:100]:
                    db.execute(
                        "DELETE FROM meals WHERE id=? AND user_id=?",
                        (int(meal_id), current_user_id()),
                    )
                restored = True
            elif kind == "meal_delete":
                meal = payload["meal"]
                db.execute("""
                    INSERT INTO meals(id,user_id,day,meal_type,food_id,quantity)
                    VALUES(?,?,?,?,?,?)
                """, (
                    int(meal["id"]), current_user_id(), meal["day"], meal["meal_type"],
                    int(meal["food_id"]), float(meal["quantity"]),
                ))
                restored = True
            elif kind == "habit_restore":
                previous = payload.get("previous")
                if previous:
                    db.execute("""
                        INSERT INTO habits(user_id,day,water,sleep,trained,appetite) VALUES(?,?,?,?,?,?)
                        ON CONFLICT(user_id,day) DO UPDATE SET water=excluded.water,sleep=excluded.sleep,
                        trained=excluded.trained,appetite=excluded.appetite
                    """, (
                        current_user_id(), day, float(previous["water"]), float(previous["sleep"]),
                        int(previous["trained"]), int(previous["appetite"]),
                    ))
                else:
                    db.execute(
                        "DELETE FROM habits WHERE user_id=? AND day=?", (current_user_id(), day)
                    )
                restored = True
            elif kind == "weight_restore":
                previous = payload.get("previous")
                if previous is None:
                    db.execute(
                        "DELETE FROM weights WHERE user_id=? AND day=?", (current_user_id(), day)
                    )
                else:
                    db.execute(
                        "INSERT OR REPLACE INTO weights(user_id,day,weight) VALUES(?,?,?)",
                        (current_user_id(), day, float(previous)),
                    )
                restored = True
            elif kind == "favorite_restore":
                db.execute(
                    "UPDATE foods SET favorite=? WHERE id=? AND user_id=?",
                    (int(payload["previous"]), int(payload["food_id"]), current_user_id()),
                )
                restored = True
            if restored:
                db.commit()
    except (KeyError, TypeError, ValueError, sqlite3.Error):
        app.logger.exception("Não foi possível desfazer a última ação.")
        restored = False

    flash("Ação desfeita. Os dados anteriores foram restaurados." if restored else "Não foi possível desfazer essa ação.")
    if undo.get("return_to") == "foods":
        return redirect(url_for("foods"))
    return redirect(url_for("dashboard", day=day))


def csrf_token() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(24)
    return session["csrf_token"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def load_user_and_check_csrf():
    user_id = session.get("user_id")
    if user_id:
        with db_conn() as db:
            account = db.execute("SELECT id,name,email,email_verified FROM users WHERE id=?", (user_id,)).fetchone()
        if account and account["email_verified"]:
            g.user = account
        else:
            session.pop("user_id", None)
            if account:
                session["pending_user_id"] = account["id"]
            g.user = None
    else:
        g.user = None

    if request.method == "POST":
        sent = request.form.get("_csrf", "")
        expected = session.get("csrf_token", "")
        if not expected or not secrets.compare_digest(sent, expected):
            abort(400, "Formulário expirado. Atualize a página e tente novamente.")

    # Antes de liberar os módulos, todo usuário realiza a avaliação inicial.
    if g.user and request.endpoint not in {
        "onboarding", "onboarding_result", "logout", "health", "static"
    }:
        with db_conn() as db:
            profile = db.execute(
                "SELECT onboarding_completed FROM settings WHERE user_id=?",
                (g.user["id"],)
            ).fetchone()
        if profile and not profile["onboarding_completed"]:
            return redirect(url_for("onboarding"))


def seed_user(db: sqlite3.Connection, user_id: int) -> None:
    db.execute("INSERT INTO settings(user_id) VALUES(?)", (user_id,))
    db.execute("INSERT INTO plan_settings(user_id) VALUES(?)", (user_id,))
    foods = [
        ("Arroz cozido",100,"g",130,2.7,28,0.3,1.6),
        ("Feijão cozido",100,"g",76,4.8,13.6,0.5,8.5),
        ("Peito de frango",100,"g",165,31,0,3.6,0),
        ("Ovo inteiro",50,"g",72,6.3,0.4,4.8,0),
        ("Leite integral",200,"ml",122,6.4,9.4,6.6,0),
        ("Banana",100,"g",89,1.1,23,0.3,2.6),
        ("Aveia",40,"g",152,5.1,27,2.8,4.2),
        ("Pasta de amendoim",30,"g",177,7.5,6,15,1.8),
        ("Macarrão cozido",100,"g",157,5.8,30.9,0.9,1.8),
        ("Azeite",10,"ml",88,0,0,10,0),
        ("Pão de forma",50,"g",132,4.5,24.5,1.8,2),
    ]
    db.executemany("""INSERT INTO foods(user_id,name,serving,unit,calories,protein,carbs,fat,fiber)
                      VALUES(?,?,?,?,?,?,?,?,?)""", [(user_id,*f) for f in foods])
    food_ids = {r["name"]: r["id"] for r in db.execute("SELECT id,name FROM foods WHERE user_id=?", (user_id,))}
    plan = [
        (food_ids["Arroz cozido"],"Arroz cru","g",180,5000,32.90,"Marmitas",0,"Rende aproximadamente 500 g cozido por dia"),
        (food_ids["Feijão cozido"],"Feijão cru","g",100,1000,9.99,"Marmitas",0,"Rende aproximadamente 250 a 300 g cozido por dia"),
        (food_ids["Peito de frango"],"Peito de frango cru","g",450,1000,21.90,"Marmitas",0,"Ajuste conforme a perda no preparo"),
        (food_ids["Leite integral"],"Leite integral","ml",1000,1000,5.80,"Café e ceia",0,""),
        (food_ids["Aveia"],"Aveia","g",100,1000,15.00,"Café e lanche",0,""),
        (food_ids["Banana"],"Banana","un",2,12,10.00,"Café e lanche",0,"Preço por dúzia; vínculo nutricional é aproximado"),
        (food_ids["Ovo inteiro"],"Ovos","un",2,30,28.00,"Lanche",0,"Preço por bandeja"),
        (food_ids["Pão de forma"],"Pão de forma","fatia",4,20,9.00,"Lanche",0,"Ajuste o número de fatias do pacote"),
        (food_ids["Pasta de amendoim"],"Pasta de amendoim","g",30,1000,35.00,"Lanche",0,""),
    ]
    db.executemany("""INSERT INTO plan_items(user_id,food_id,name,unit,daily_qty,package_qty,package_price,category,current_stock,notes)
                      VALUES(?,?,?,?,?,?,?,?,?,?)""", [(user_id,*x) for x in plan])
    starter_keys = (
        "agachamento_livre", "supino_reto", "remada_baixa",
        "desenvolvimento_militar", "levantamento_terra_romeno", "rosca_direta",
    )
    for catalog_key in starter_keys:
        exercise = db.execute(
            "SELECT catalog_key,name,primary_group,description FROM exercise_catalog WHERE catalog_key=?",
            (catalog_key,),
        ).fetchone()
        if exercise:
            db.execute(
                """INSERT INTO exercises(user_id,name,muscle,description,catalog_key)
                   VALUES(?,?,?,?,?)""",
                (user_id, exercise["name"], exercise["primary_group"],
                 exercise["description"], exercise["catalog_key"]),
            )


def get_settings(db: sqlite3.Connection, user_id: int):
    return db.execute("SELECT * FROM settings WHERE user_id=?", (user_id,)).fetchone()


def daily_totals(db: sqlite3.Connection, user_id: int, day: str) -> dict:
    row = db.execute("""
        SELECT COALESCE(SUM(f.calories*m.quantity/f.serving),0) calories,
               COALESCE(SUM(f.protein*m.quantity/f.serving),0) protein,
               COALESCE(SUM(f.carbs*m.quantity/f.serving),0) carbs,
               COALESCE(SUM(f.fat*m.quantity/f.serving),0) fat,
               COALESCE(SUM(f.fiber*m.quantity/f.serving),0) fiber
        FROM meals m JOIN foods f ON f.id=m.food_id
        WHERE m.user_id=? AND m.day=?
    """, (user_id, day)).fetchone()
    return dict(row)


def quick_food_choices(db: sqlite3.Connection, user_id: int, limit: int = 6) -> list[dict]:
    """Favoritos primeiro, seguidos pelos alimentos usados mais recentemente."""
    favorites = db.execute(
        "SELECT * FROM foods WHERE user_id=? AND favorite=1 ORDER BY name", (user_id,)
    ).fetchall()
    recent_meals = db.execute("""
        SELECT m.food_id,m.quantity,m.meal_type,f.*
        FROM meals m JOIN foods f ON f.id=m.food_id
        WHERE m.user_id=? ORDER BY m.day DESC,m.id DESC LIMIT 100
    """, (user_id,)).fetchall()
    fallback = db.execute(
        "SELECT * FROM foods WHERE user_id=? ORDER BY name", (user_id,)
    ).fetchall()

    result: list[dict] = []
    seen: set[int] = set()
    recent_by_food: dict[int, sqlite3.Row] = {}
    for row in recent_meals:
        recent_by_food.setdefault(row["food_id"], row)

    def add_food(row, usage=None):
        food_id = int(row["id"])
        if food_id in seen or len(result) >= limit:
            return
        item = dict(row)
        item["quick_quantity"] = usage["quantity"] if usage else row["serving"]
        item["quick_meal_type"] = usage["meal_type"] if usage else "Refeição"
        result.append(item)
        seen.add(food_id)

    for row in favorites:
        add_food(row, recent_by_food.get(row["id"]))
    for row in recent_meals:
        add_food(row, row)
    for row in fallback:
        add_food(row, recent_by_food.get(row["id"]))
    return result


def weekly_summary(db: sqlite3.Connection, user_id: int, settings, anchor_day: str) -> dict:
    """Compara os sete dias encerrados na data escolhida com a semana anterior."""
    anchor = date.fromisoformat(anchor_day)
    current_start = anchor - timedelta(days=6)
    previous_start = anchor - timedelta(days=13)
    previous_end = anchor - timedelta(days=7)

    def nutrition_between(start: date, end: date) -> dict:
        rows = db.execute("""
            SELECT m.day,
                   SUM(f.calories*m.quantity/f.serving) calories,
                   SUM(f.protein*m.quantity/f.serving) protein
            FROM meals m JOIN foods f ON f.id=m.food_id
            WHERE m.user_id=? AND m.day BETWEEN ? AND ?
            GROUP BY m.day ORDER BY m.day
        """, (user_id, start.isoformat(), end.isoformat())).fetchall()
        days = len(rows)
        calories = sum(row["calories"] or 0 for row in rows)
        protein = sum(row["protein"] or 0 for row in rows)
        target_days = sum(
            1 for row in rows
            if settings["calories"] * .9 <= (row["calories"] or 0) <= settings["calories"] * 1.1
        )
        return {
            "days": days,
            "avg_calories": calories / days if days else 0,
            "avg_protein": protein / days if days else 0,
            "target_days": target_days,
        }

    current = nutrition_between(current_start, anchor)
    previous = nutrition_between(previous_start, previous_end)
    habits = db.execute("""
        SELECT COUNT(*) days,AVG(NULLIF(water,0)) water,AVG(NULLIF(sleep,0)) sleep,SUM(trained) trained
        FROM habits WHERE user_id=? AND day BETWEEN ? AND ?
    """, (user_id, current_start.isoformat(), anchor.isoformat())).fetchone()
    week_weights = db.execute("""
        SELECT day,weight FROM weights
        WHERE user_id=? AND day BETWEEN ? AND ? ORDER BY day
    """, (user_id, current_start.isoformat(), anchor.isoformat())).fetchall()

    comparison = "Registre pelo menos três dias para liberar uma comparação mais confiável."
    comparison_level = "info"
    if current["days"] >= 3:
        if previous["days"]:
            calorie_change = current["avg_calories"] - previous["avg_calories"]
            protein_change = current["avg_protein"] - previous["avg_protein"]
            comparison = (
                f"Em relação à semana anterior: {calorie_change:+.0f} kcal e "
                f"{protein_change:+.0f} g de proteína por dia registrado."
            )
        elif current["avg_calories"] < settings["calories"] * .85:
            comparison = "Sua média calórica ainda está abaixo da meta. Tente aumentar gradualmente a consistência."
            comparison_level = "warn"
        elif current["avg_protein"] < settings["protein"] * .85:
            comparison = "As calorias estão próximas, mas a proteína ainda pode melhorar nesta semana."
            comparison_level = "warn"
        else:
            comparison = "Boa semana: suas médias registradas estão próximas das metas configuradas."
            comparison_level = "good"

    weight_change = None
    if len(week_weights) >= 2:
        weight_change = week_weights[-1]["weight"] - week_weights[0]["weight"]

    return {
        **current,
        "period": f"{current_start.strftime('%d/%m')} a {anchor.strftime('%d/%m')}",
        "calorie_percent": current["avg_calories"] / settings["calories"] * 100 if settings["calories"] else 0,
        "protein_percent": current["avg_protein"] / settings["protein"] * 100 if settings["protein"] else 0,
        "habit_days": habits["days"] or 0,
        "avg_water": habits["water"] or 0,
        "avg_sleep": habits["sleep"] or 0,
        "training_days": habits["trained"] or 0,
        "weight_change": weight_change,
        "comparison": comparison,
        "comparison_level": comparison_level,
    }


def weight_predictions(weights, milestones=(70,75,80,85)) -> tuple[list[dict], float | None]:
    if len(weights) < 2:
        return ([{"goal": g, "text": "Registre pelo menos dois pesos em datas diferentes."} for g in milestones], None)
    first, last = weights[0], weights[-1]
    days = (date.fromisoformat(last["day"]) - date.fromisoformat(first["day"])).days
    if days <= 0:
        return ([{"goal": g, "text": "Ainda não há intervalo suficiente."} for g in milestones], None)
    weekly_rate = (last["weight"] - first["weight"]) / days * 7
    result = []
    for goal in milestones:
        if last["weight"] >= goal:
            result.append({"goal": goal, "text": "Meta já alcançada."})
        elif weekly_rate <= 0.03:
            result.append({"goal": goal, "text": "Sem tendência positiva suficiente para estimar."})
        else:
            weeks = (goal - last["weight"]) / weekly_rate
            target_date = date.fromisoformat(last["day"]) + timedelta(days=round(weeks*7))
            result.append({"goal": goal, "text": f"Estimativa: {target_date.strftime('%d/%m/%Y')} ({weeks:.1f} semanas)"})
    return result, weekly_rate


def automatic_insights(db: sqlite3.Connection, user_id: int, settings, weights) -> list[dict]:
    since = (date.today() - timedelta(days=6)).isoformat()
    nutrition = db.execute("""
      SELECT COUNT(DISTINCT m.day) logged_days,
             COALESCE(SUM(f.calories*m.quantity/f.serving),0) calories,
             COALESCE(SUM(f.protein*m.quantity/f.serving),0) protein
      FROM meals m JOIN foods f ON f.id=m.food_id
      WHERE m.user_id=? AND m.day>=?
    """, (user_id, since)).fetchone()
    habits = db.execute("""SELECT AVG(water) water, AVG(sleep) sleep, AVG(appetite) appetite,
                            SUM(trained) trained, COUNT(*) days
                            FROM habits WHERE user_id=? AND day>=?""", (user_id, since)).fetchone()
    insights = []
    logged = nutrition["logged_days"] or 0
    if logged:
        avg_cal = nutrition["calories"] / logged
        avg_pro = nutrition["protein"] / logged
        if avg_cal < settings["calories"] * .85:
            insights.append({"level":"warn","title":"Calorias abaixo da meta","text":f"Sua média nos dias registrados foi {avg_cal:.0f} kcal. Faltaram cerca de {settings['calories']-avg_cal:.0f} kcal por dia."})
        else:
            insights.append({"level":"good","title":"Boa consistência calórica","text":f"Média de {avg_cal:.0f} kcal nos dias registrados nesta semana."})
        if avg_pro < settings["protein"] * .85:
            insights.append({"level":"warn","title":"Proteína pode melhorar","text":f"Média de {avg_pro:.0f} g; sua meta configurada é {settings['protein']} g."})
        else:
            insights.append({"level":"good","title":"Proteína bem encaminhada","text":f"Média de {avg_pro:.0f} g nos dias registrados."})
    else:
        insights.append({"level":"info","title":"Comece pelo registro","text":"Registre as refeições de alguns dias para o TITAN analisar sua alimentação."})
    if habits and habits["days"]:
        if habits["sleep"] and habits["sleep"] < 7:
            insights.append({"level":"warn","title":"Sono abaixo de 7 horas","text":f"Média registrada de {habits['sleep']:.1f} horas. Recuperação também influencia treino e apetite."})
        if habits["water"] and habits["water"] < settings["water"] * .8:
            insights.append({"level":"info","title":"Hidratação abaixo da meta","text":f"Média de {habits['water']:.1f} L para uma meta de {settings['water']:.1f} L."})
    predictions, rate = weight_predictions(weights)
    if rate is not None:
        if rate > .7:
            insights.append({"level":"warn","title":"Peso subindo rapidamente","text":f"Tendência aproximada de {rate:.2f} kg/semana. Verifique se a evolução está confortável e sustentável."})
        elif rate > .05:
            insights.append({"level":"good","title":"Tendência de ganho detectada","text":f"A tendência entre seus registros é de aproximadamente {rate:.2f} kg/semana."})
    return insights[:5]



def round_step(value: float, step: int = 5) -> int:
    return int(round(value / step) * step)


def first_stage_goal(weight: float, final_goal: float, goal_type: str) -> float:
    if goal_type == "gain":
        checkpoint = math.floor(weight / 5) * 5 + 5
        return min(final_goal, checkpoint)
    if goal_type == "loss":
        checkpoint = math.ceil(weight / 5) * 5 - 5
        return max(final_goal, checkpoint)
    return weight


def calculate_initial_plan(form) -> dict:
    age = int(form["age"])
    height_cm = float(form["height_cm"])
    weight = float(form["weight"])
    sex = form["sex"]
    activity_level = form["activity_level"]
    goal_type = form["goal_type"]
    training_days = int(form["training_days"])
    appetite_level = form["appetite_level"]
    meals_per_day = int(form["meals_per_day"])
    pace = form["pace"]
    budget_monthly = float(form.get("budget_monthly") or 0)
    restrictions = form.get("restrictions", "").strip()

    if not 18 <= age <= 90:
        raise ValueError("O cálculo automático desta versão é destinado a adultos entre 18 e 90 anos.")
    if not 130 <= height_cm <= 230 or not 35 <= weight <= 300:
        raise ValueError("Confira a altura e o peso informados.")
    if sex not in {"male", "female"}:
        raise ValueError("Selecione a referência metabólica.")
    if goal_type not in {"gain", "maintain", "loss"}:
        raise ValueError("Selecione um objetivo válido.")

    activity_factors = {
        "sedentary": 1.20,
        "light": 1.375,
        "moderate": 1.55,
        "high": 1.725,
        "very_high": 1.90,
    }
    factor = activity_factors.get(activity_level, 1.20)
    # Considera também a frequência de treino planejada, sem somar duas vezes:
    # usa o maior fator entre a rotina declarada e a frequência semanal.
    training_factors = {0: 1.20, 1: 1.25, 2: 1.30, 3: 1.375, 4: 1.45, 5: 1.55, 6: 1.65, 7: 1.725}
    factor = max(factor, training_factors.get(training_days, 1.20))

    # Equação de Mifflin-St Jeor para estimar o gasto energético de repouso.
    sex_constant = 5 if sex == "male" else -161
    bmr = 10 * weight + 6.25 * height_cm - 5 * age + sex_constant
    tdee = bmr * factor

    adjustments = {
        "gain": {"slow": 200, "moderate": 300, "fast": 450},
        "maintain": {"slow": 0, "moderate": 0, "fast": 0},
        "loss": {"slow": -300, "moderate": -450, "fast": -650},
    }
    weekly_rates = {
        "gain": {"slow": .20, "moderate": .35, "fast": .50},
        "maintain": {"slow": 0, "moderate": 0, "fast": 0},
        "loss": {"slow": .25, "moderate": .50, "fast": .75},
    }
    adjustment = adjustments[goal_type].get(pace, adjustments[goal_type]["moderate"])
    weekly_target = weekly_rates[goal_type].get(pace, weekly_rates[goal_type]["moderate"])

    # Para apetite baixo, evita começar com um salto excessivo de calorias.
    if goal_type == "gain" and appetite_level == "low":
        adjustment = min(adjustment, 300)
        weekly_target = min(weekly_target, .35)

    calories = max(1200, round_step(tdee + adjustment, 50))
    if goal_type == "gain":
        protein_factor = 1.8 if training_days >= 3 else 1.6
        fat_factor = .9
    elif goal_type == "loss":
        protein_factor = 2.0 if training_days >= 2 else 1.7
        fat_factor = .8
    else:
        protein_factor = 1.6 if training_days >= 2 else 1.4
        fat_factor = .9

    protein = max(70, round_step(weight * protein_factor, 5))
    fat = max(50, round_step(weight * fat_factor, 5))
    carbs = max(80, round_step((calories - protein * 4 - fat * 9) / 4, 5))
    water = round(min(4.5, max(2.0, weight * .035 + (.3 if training_days >= 4 else 0))), 1)

    if goal_type == "maintain":
        final_goal = weight
    else:
        final_goal = float(form["final_goal"])
        if goal_type == "gain" and final_goal <= weight:
            raise ValueError("Para ganhar peso, a meta final precisa ser maior que o peso atual.")
        if goal_type == "loss" and final_goal >= weight:
            raise ValueError("Para reduzir peso, a meta final precisa ser menor que o peso atual.")
        if not 35 <= final_goal <= 300:
            raise ValueError("Confira a meta final informada.")

    goal_weight = first_stage_goal(weight, final_goal, goal_type)
    difference = abs(final_goal - weight)
    estimated_weeks = difference / weekly_target if weekly_target > 0 else 0

    return {
        "age": age,
        "height": height_cm / 100,
        "weight": weight,
        "sex": sex,
        "activity_level": activity_level,
        "goal_type": goal_type,
        "training_days": training_days,
        "appetite_level": appetite_level,
        "meals_per_day": meals_per_day,
        "budget_monthly": budget_monthly,
        "restrictions": restrictions,
        "bmr": round(bmr),
        "tdee": round(tdee),
        "calories": calories,
        "protein": protein,
        "carbs": carbs,
        "fat": fat,
        "water": water,
        "weekly_target": weekly_target,
        "goal_weight": goal_weight,
        "final_goal": final_goal,
        "estimated_weeks": estimated_weeks,
        "pace": pace,
    }


def reminder_times(meals_per_day: int) -> list[str]:
    schedules = {
        3: ["08:00", "13:00", "20:00"],
        4: ["07:30", "12:00", "16:00", "20:30"],
        5: ["07:30", "10:30", "13:30", "17:00", "20:30"],
        6: ["07:00", "10:00", "13:00", "16:00", "19:00", "22:00"],
    }
    return schedules.get(meals_per_day, schedules[4])


def allowed_image(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGES


def save_user_image(file_storage, prefix: str) -> str:
    if not file_storage or not file_storage.filename:
        return ""
    if not allowed_image(file_storage.filename):
        raise ValueError("Formato de imagem não permitido. Use JPG, PNG ou WEBP.")
    ext = file_storage.filename.rsplit(".", 1)[1].lower()
    user_folder = UPLOAD_DIR / str(current_user_id())
    user_folder.mkdir(parents=True, exist_ok=True)
    filename = secure_filename(f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}.{ext}")
    file_storage.save(user_folder / filename)
    return filename


@app.route("/health")
def health():
    return {"status": "ok", "database": DB_PATH.exists()}


@app.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        password_confirm = request.form.get("password_confirm", "")
        if len(name) < 2 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            flash("Preencha um nome e um e-mail válido.")
            return render_template("register.html", form_name=name, form_email=email)
        errors = password_errors(password, name, email)
        if errors:
            flash("A senha ainda precisa de: " + ", ".join(errors) + ".")
            return render_template("register.html", form_name=name, form_email=email)
        if password != password_confirm:
            flash("A confirmação da senha não corresponde.")
            return render_template("register.html", form_name=name, form_email=email)
        try:
            with db_conn() as db:
                cur = db.execute("INSERT INTO users(name,email,password_hash,created_at,email_verified) VALUES(?,?,?,?,0)",
                                 (name, email, generate_password_hash(password), datetime.now().isoformat(timespec="seconds")))
                user_id = cur.lastrowid
                seed_user(db, user_id)
                code = issue_verification_code(db, user_id)
                db.commit()
        except sqlite3.IntegrityError:
            flash("Este e-mail já está cadastrado.")
            return render_template("register.html", form_name=name, form_email=email)
        except sqlite3.Error:
            app.logger.exception("Falha de banco de dados durante a criação da conta.")
            flash("Não foi possível criar a conta no banco de dados. Tente novamente em instantes.")
            return render_template("register.html", form_name=name, form_email=email)
        except Exception:
            app.logger.exception("Falha inesperada durante a criação da conta.")
            flash("Não foi possível concluir o cadastro. Nenhuma senha foi enviada por e-mail.")
            return render_template("register.html", form_name=name, form_email=email)
        session.clear()
        session["pending_user_id"] = user_id
        csrf_token()
        if send_verification_email(name, email, code):
            flash("Enviamos um código de 6 dígitos para confirmar seu e-mail.")
        else:
            flash("Sua conta foi criada, mas o envio falhou. Verifique a configuração de e-mail e use Reenviar código.")
        return redirect(url_for("verify_email"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        with db_conn() as db:
            user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not user or not check_password_hash(user["password_hash"], request.form["password"]):
            flash("E-mail ou senha incorretos.")
            return render_template("login.html")
        if not user["email_verified"]:
            should_send = False
            with db_conn() as db:
                verification = db.execute(
                    "SELECT * FROM email_verifications WHERE user_id=?", (user["id"],)
                ).fetchone()
                if not verification or parse_utc(verification["expires_at"]) <= utc_now():
                    code = issue_verification_code(db, user["id"])
                    db.commit()
                    should_send = True
            session.clear()
            session["pending_user_id"] = user["id"]
            csrf_token()
            if should_send:
                if send_verification_email(user["name"], user["email"], code):
                    flash("Enviamos um novo código para confirmar seu e-mail.")
                else:
                    flash("Não foi possível enviar o código agora. Tente reenviar em instantes.")
            else:
                flash("Sua conta ainda precisa da confirmação por e-mail.")
            return redirect(url_for("verify_email"))
        session.clear()
        session["user_id"] = user["id"]
        csrf_token()
        return redirect(safe_next_url(request.args.get("next")) or url_for("dashboard"))
    return render_template("login.html")


@app.route("/esqueci-senha", methods=["GET", "POST"])
def forgot_password():
    if g.user:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        mail_job = None
        if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            try:
                with db_conn() as db:
                    user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
                    if user and user["email_verified"]:
                        reset = db.execute(
                            "SELECT * FROM password_resets WHERE user_id=?", (user["id"],)
                        ).fetchone()
                        can_send = True
                        if reset:
                            elapsed = (utc_now() - parse_utc(reset["last_sent_at"])).total_seconds()
                            can_send = elapsed >= VERIFICATION_RESEND_SECONDS
                        if can_send:
                            code = issue_password_reset_code(db, user["id"])
                            db.commit()
                            mail_job = (user["name"], user["email"], code)
            except Exception:
                app.logger.exception("Falha ao iniciar a recuperação de senha.")
        if mail_job:
            send_password_reset_email(*mail_job)
        session["password_reset_email"] = email
        flash("Se existir uma conta confirmada com esse e-mail, enviamos um código de recuperação.")
        return redirect(url_for("reset_password"))
    return render_template("forgot_password.html")


@app.route("/redefinir-senha", methods=["GET", "POST"])
def reset_password():
    if g.user:
        return redirect(url_for("dashboard"))
    email = session.get("password_reset_email", "")
    if not email:
        flash("Informe seu e-mail para iniciar a recuperação.")
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        code = request.form.get("code", "").strip()
        new_password = request.form.get("password", "")
        confirmation = request.form.get("password_confirm", "")
        with db_conn() as db:
            user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            reset = db.execute(
                "SELECT * FROM password_resets WHERE user_id=?", (user["id"],)
            ).fetchone() if user else None

        if not user or not reset:
            flash("Código inválido ou expirado. Solicite um novo código.")
        elif parse_utc(reset["expires_at"]) <= utc_now():
            flash("Esse código expirou. Solicite um novo código.")
        elif reset["attempts"] >= VERIFICATION_MAX_ATTEMPTS:
            flash("Limite de tentativas atingido. Solicite um novo código.")
        elif not re.fullmatch(r"\d{6}", code):
            flash("Digite os 6 números do código.")
        elif not secrets.compare_digest(reset["code_hash"], password_reset_code_hash(user["id"], code)):
            attempts = reset["attempts"] + 1
            with db_conn() as db:
                db.execute("UPDATE password_resets SET attempts=? WHERE user_id=?", (attempts, user["id"]))
                db.commit()
            remaining = max(0, VERIFICATION_MAX_ATTEMPTS - attempts)
            flash(f"Código incorreto. Restam {remaining} tentativa(s).")
        else:
            errors = password_errors(new_password, user["name"], user["email"])
            if errors:
                flash("A nova senha ainda precisa de: " + ", ".join(errors) + ".")
            elif new_password != confirmation:
                flash("A confirmação da nova senha não corresponde.")
            elif check_password_hash(user["password_hash"], new_password):
                flash("A nova senha precisa ser diferente da senha anterior.")
            else:
                with db_conn() as db:
                    db.execute(
                        "UPDATE users SET password_hash=? WHERE id=?",
                        (generate_password_hash(new_password), user["id"]),
                    )
                    db.execute("DELETE FROM password_resets WHERE user_id=?", (user["id"],))
                    db.commit()
                session.clear()
                flash("Senha alterada com segurança. Entre usando sua nova senha.")
                return redirect(url_for("login"))

    return render_template(
        "reset_password.html",
        masked_email=mask_email(email),
        ttl_minutes=VERIFICATION_TTL_MINUTES,
    )


@app.post("/redefinir-senha/reenviar")
def resend_password_reset():
    if g.user:
        return redirect(url_for("dashboard"))
    email = session.get("password_reset_email", "")
    if not email:
        return redirect(url_for("forgot_password"))
    mail_job = None
    try:
        with db_conn() as db:
            user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            if user and user["email_verified"]:
                reset = db.execute(
                    "SELECT * FROM password_resets WHERE user_id=?", (user["id"],)
                ).fetchone()
                can_send = True
                if reset:
                    elapsed = (utc_now() - parse_utc(reset["last_sent_at"])).total_seconds()
                    can_send = elapsed >= VERIFICATION_RESEND_SECONDS
                if can_send:
                    code = issue_password_reset_code(db, user["id"])
                    db.commit()
                    mail_job = (user["name"], user["email"], code)
    except Exception:
        app.logger.exception("Falha ao reenviar código de recuperação.")
    if mail_job:
        send_password_reset_email(*mail_job)
    flash("Se o endereço estiver apto, um novo código foi enviado. Aguarde antes de tentar novamente.")
    return redirect(url_for("reset_password"))


@app.route("/verificar-email", methods=["GET", "POST"])
def verify_email():
    if g.user:
        return redirect(url_for("dashboard"))
    user_id = session.get("pending_user_id")
    if not user_id:
        flash("Entre na sua conta para continuar a confirmação.")
        return redirect(url_for("login"))

    with db_conn() as db:
        user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        verification = db.execute(
            "SELECT * FROM email_verifications WHERE user_id=?", (user_id,)
        ).fetchone()
    if not user:
        session.clear()
        return redirect(url_for("register"))
    if user["email_verified"]:
        session.clear()
        session["user_id"] = user["id"]
        csrf_token()
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        code = request.form.get("code", "").strip()
        if not verification:
            flash("Não há código ativo. Solicite um novo código.")
        elif parse_utc(verification["expires_at"]) <= utc_now():
            flash("Esse código expirou. Solicite um novo código.")
        elif verification["attempts"] >= VERIFICATION_MAX_ATTEMPTS:
            flash("Limite de tentativas atingido. Solicite um novo código.")
        elif not re.fullmatch(r"\d{6}", code):
            flash("Digite os 6 números do código.")
        elif secrets.compare_digest(verification["code_hash"], verification_code_hash(user_id, code)):
            with db_conn() as db:
                db.execute("UPDATE users SET email_verified=1 WHERE id=?", (user_id,))
                db.execute("DELETE FROM email_verifications WHERE user_id=?", (user_id,))
                db.commit()
            session.clear()
            session["user_id"] = user_id
            csrf_token()
            flash("E-mail confirmado com segurança. Agora vamos calcular seu plano inicial.")
            return redirect(url_for("onboarding"))
        else:
            attempts = verification["attempts"] + 1
            with db_conn() as db:
                db.execute("UPDATE email_verifications SET attempts=? WHERE user_id=?", (attempts, user_id))
                db.commit()
            remaining = max(0, VERIFICATION_MAX_ATTEMPTS - attempts)
            flash(f"Código incorreto. Restam {remaining} tentativa(s).")

    return render_template(
        "verify_email.html",
        masked_email=mask_email(user["email"]),
        ttl_minutes=VERIFICATION_TTL_MINUTES,
    )


@app.post("/verificar-email/reenviar")
def resend_verification():
    if g.user:
        return redirect(url_for("dashboard"))
    user_id = session.get("pending_user_id")
    if not user_id:
        return redirect(url_for("login"))
    with db_conn() as db:
        user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        verification = db.execute(
            "SELECT * FROM email_verifications WHERE user_id=?", (user_id,)
        ).fetchone()
        if not user:
            session.clear()
            return redirect(url_for("register"))
        if verification:
            elapsed = (utc_now() - parse_utc(verification["last_sent_at"])).total_seconds()
            if elapsed < VERIFICATION_RESEND_SECONDS:
                wait = max(1, int(VERIFICATION_RESEND_SECONDS - elapsed))
                flash(f"Aguarde {wait} segundo(s) antes de pedir outro código.")
                return redirect(url_for("verify_email"))
        code = issue_verification_code(db, user_id)
        db.commit()
    if send_verification_email(user["name"], user["email"], code):
        flash("Um novo código foi enviado. O código anterior não é mais válido.")
    else:
        flash("Não foi possível enviar o código. Confira a configuração de e-mail e tente novamente.")
    return redirect(url_for("verify_email"))


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))



@app.route("/avaliacao", methods=["GET", "POST"])
@login_required
def onboarding():
    user_id = current_user_id()
    with db_conn() as db:
        s = get_settings(db, user_id)
        if request.method == "POST":
            try:
                plan = calculate_initial_plan(request.form)
            except (ValueError, KeyError, TypeError) as exc:
                flash(str(exc) if str(exc) else "Confira as respostas do questionário.")
                return render_template("onboarding.html", s=s, hide_nav=True)

            db.execute("""UPDATE settings SET
                age=?,height=?,start_weight=?,goal_weight=?,final_goal=?,calories=?,protein=?,carbs=?,fat=?,water=?,weekly_target=?,
                sex=?,activity_level=?,goal_type=?,training_days=?,appetite_level=?,meals_per_day=?,budget_monthly=?,restrictions=?,
                bmr=?,tdee=?,onboarding_completed=1,calculation_version='TITAN-1.0'
                WHERE user_id=?""", (
                plan["age"], plan["height"], plan["weight"], plan["goal_weight"], plan["final_goal"],
                plan["calories"], plan["protein"], plan["carbs"], plan["fat"], plan["water"], plan["weekly_target"],
                plan["sex"], plan["activity_level"], plan["goal_type"], plan["training_days"], plan["appetite_level"],
                plan["meals_per_day"], plan["budget_monthly"], plan["restrictions"], plan["bmr"], plan["tdee"], user_id
            ))
            db.execute("""INSERT INTO weights(user_id,day,weight) VALUES(?,?,?)
                          ON CONFLICT(user_id,day) DO UPDATE SET weight=excluded.weight""",
                       (user_id, today(), plan["weight"]))
            db.execute("UPDATE plan_settings SET meals_per_day=? WHERE user_id=?",
                       (plan["meals_per_day"], user_id))

            # Cria horários iniciais sem apagar lembretes personalizados.
            db.execute("DELETE FROM reminders WHERE user_id=? AND title LIKE 'Refeição % (TITAN)'", (user_id,))
            for index, time_value in enumerate(reminder_times(plan["meals_per_day"]), start=1):
                db.execute("INSERT INTO reminders(user_id,title,time,days,enabled) VALUES(?,?,?,?,1)",
                           (user_id, f"Refeição {index} (TITAN)", time_value, "Todos os dias"))
            db.commit()
            session["show_onboarding_result"] = True
            return redirect(url_for("onboarding_result"))
    return render_template("onboarding.html", s=s, hide_nav=True)


@app.get("/avaliacao/resultado")
@login_required
def onboarding_result():
    with db_conn() as db:
        s = get_settings(db, current_user_id())
    if not s["onboarding_completed"]:
        return redirect(url_for("onboarding"))
    activity_names = {
        "sedentary": "Sedentário",
        "light": "Levemente ativo",
        "moderate": "Moderadamente ativo",
        "high": "Muito ativo",
        "very_high": "Extremamente ativo",
    }
    goal_names = {"gain": "Ganhar peso e massa muscular", "maintain": "Manter o peso", "loss": "Reduzir peso"}
    weeks = abs(s["final_goal"] - s["start_weight"]) / s["weekly_target"] if s["weekly_target"] else 0
    daily_budget = s["budget_monthly"] / 30 if s["budget_monthly"] else 0
    meal_budget = daily_budget / s["meals_per_day"] if daily_budget else 0
    tips = []
    if s["goal_type"] == "gain" and s["appetite_level"] == "low":
        tips.append("Como seu apetite é baixo, o plano começa com superávit moderado e prioriza alimentos mais densos e opções líquidas.")
    if s["training_days"] < 2 and s["goal_type"] == "gain":
        tips.append("Para favorecer ganho muscular, registre e progrida nos treinos de força; apenas aumentar calorias não garante que o peso ganho seja músculo.")
    if s["budget_monthly"]:
        tips.append(f"Seu limite inicial é de aproximadamente R$ {daily_budget:.2f} por dia e R$ {meal_budget:.2f} por refeição.")
    tips.append("Depois de 14 dias de registros, o TITAN compara peso e consumo real para sugerir ajustes graduais.")
    return render_template("onboarding_result.html", s=s, weeks=weeks, tips=tips,
                           activity_name=activity_names.get(s["activity_level"], s["activity_level"]),
                           goal_name=goal_names.get(s["goal_type"], s["goal_type"]),
                           daily_budget=daily_budget, meal_budget=meal_budget, hide_nav=True)


@app.route("/")
@login_required
def dashboard():
    user_id = current_user_id()
    day = tracking_day(request.args.get("day", today()))
    previous_day = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    with db_conn() as db:
        s = get_settings(db, user_id)
        totals = daily_totals(db, user_id, day)
        meals = db.execute("""SELECT m.*,f.name,f.serving,f.unit,f.calories,f.protein,f.carbs,f.fat,f.fiber
                              FROM meals m JOIN foods f ON f.id=m.food_id
                              WHERE m.user_id=? AND m.day=? ORDER BY m.id DESC""", (user_id, day)).fetchall()
        foods = db.execute("SELECT * FROM foods WHERE user_id=? ORDER BY favorite DESC,name", (user_id,)).fetchall()
        weights = db.execute("SELECT day,weight FROM weights WHERE user_id=? ORDER BY day", (user_id,)).fetchall()
        latest_weight = weights[-1]["weight"] if weights else s["start_weight"]
        habit = db.execute("SELECT * FROM habits WHERE user_id=? AND day=?", (user_id, day)).fetchone()
        workouts = db.execute("""SELECT w.*,e.name exercise FROM workouts w JOIN exercises e ON e.id=w.exercise_id
                               WHERE w.user_id=? AND w.day=? ORDER BY w.id DESC""", (user_id, day)).fetchall()
        previous_meals = db.execute(
            "SELECT COUNT(*) total FROM meals WHERE user_id=? AND day=?", (user_id, previous_day)
        ).fetchone()["total"]
        quick_foods = quick_food_choices(db, user_id)
        week = weekly_summary(db, user_id, s, day)
        insights = automatic_insights(db, user_id, s, weights)
        predictions, weekly_rate = weight_predictions(weights)
    bmi = latest_weight / max(.1, s["height"] ** 2)
    denominator = max(.1, s["goal_weight"] - s["start_weight"])
    progress = max(0, min(100, (latest_weight - s["start_weight"]) / denominator * 100))
    return render_template("dashboard.html", s=s, totals=totals, meals=meals, foods_all=foods,
                           day=day, weights=weights, latest_weight=latest_weight, habit=habit,
                           workouts=workouts, insights=insights, predictions=predictions,
                           weekly_rate=weekly_rate, bmi=bmi, progress=progress,
                           quick_foods=quick_foods, previous_meals=previous_meals,
                           previous_day=previous_day, week=week)


@app.route("/foods", methods=["GET", "POST"])
@login_required
def foods():
    user_id = current_user_id()
    with db_conn() as db:
        if request.method == "POST":
            try:
                serving = float(request.form["serving"])
                nutrients = [float(request.form.get(key) or 0) for key in ("calories", "protein", "carbs", "fat", "fiber")]
            except (KeyError, ValueError):
                flash("Preencha os valores nutricionais usando números válidos.")
                return redirect(url_for("foods"))
            if serving <= 0 or any(value < 0 for value in nutrients):
                flash("A porção deve ser maior que zero e os nutrientes não podem ser negativos.")
                return redirect(url_for("foods"))
            db.execute("""INSERT INTO foods(user_id,name,serving,unit,calories,protein,carbs,fat,fiber)
                          VALUES(?,?,?,?,?,?,?,?,?)""", (
                user_id, request.form["name"].strip(), serving, request.form["unit"], *nutrients
            ))
            db.commit()
            flash("Alimento cadastrado com informações nutricionais.")
            return redirect(url_for("foods"))
        rows = db.execute("SELECT * FROM foods WHERE user_id=? ORDER BY favorite DESC,name", (user_id,)).fetchall()
    return render_template("foods.html", foods=rows)


@app.post("/foods/edit/<int:item_id>")
@login_required
def edit_food(item_id):
    try:
        serving = float(request.form["serving"])
        nutrients = [float(request.form.get(key) or 0) for key in ("calories", "protein", "carbs", "fat", "fiber")]
    except (KeyError, ValueError):
        flash("Preencha os valores nutricionais usando números válidos.")
        return redirect(url_for("foods"))
    if serving <= 0 or any(value < 0 for value in nutrients):
        flash("A porção deve ser maior que zero e os nutrientes não podem ser negativos.")
        return redirect(url_for("foods"))
    with db_conn() as db:
        db.execute("""UPDATE foods SET name=?,serving=?,unit=?,calories=?,protein=?,carbs=?,fat=?,fiber=?
                      WHERE id=? AND user_id=?""", (
            request.form["name"].strip(), serving, request.form["unit"],
            *nutrients, item_id, current_user_id()
        ))
        db.commit()
    flash("Informações do alimento atualizadas.")
    return redirect(url_for("foods"))


@app.post("/foods/delete/<int:item_id>")
@login_required
def delete_food(item_id):
    with db_conn() as db:
        used = db.execute("SELECT COUNT(*) n FROM meals WHERE user_id=? AND food_id=?", (current_user_id(), item_id)).fetchone()["n"]
        if used:
            flash("Este alimento já possui refeições registradas e não pode ser excluído.")
        else:
            db.execute("DELETE FROM foods WHERE id=? AND user_id=?", (item_id, current_user_id()))
            db.commit()
            flash("Alimento excluído.")
    return redirect(url_for("foods"))


@app.post("/foods/favorite/<int:item_id>")
@login_required
def favorite_food(item_id):
    return_to = "dashboard" if request.form.get("return_to") == "dashboard" else "foods"
    day = tracking_day(request.form.get("day"))
    with db_conn() as db:
        food = db.execute(
            "SELECT favorite FROM foods WHERE id=? AND user_id=?", (item_id, current_user_id())
        ).fetchone()
        if not food:
            abort(404)
        favorite = 0 if food["favorite"] else 1
        db.execute(
            "UPDATE foods SET favorite=? WHERE id=? AND user_id=?",
            (favorite, item_id, current_user_id()),
        )
        db.commit()
    remember_undo(
        "favorite_restore",
        {"food_id": item_id, "previous": int(food["favorite"]), "day": day},
        "Favorito atualizado.",
        return_to,
    )
    if return_to == "dashboard":
        return redirect(url_for("dashboard", day=day))
    return redirect(url_for("foods"))


@app.post("/meal")
@login_required
def add_meal():
    day = tracking_day(request.form.get("day"))
    try:
        quantity = float(request.form["quantity"])
    except (KeyError, ValueError):
        quantity = 0
    if quantity <= 0:
        flash("Informe uma quantidade maior que zero.")
        return redirect(url_for("dashboard", day=day))
    with db_conn() as db:
        food = db.execute("SELECT id FROM foods WHERE id=? AND user_id=?", (request.form["food_id"], current_user_id())).fetchone()
        if not food:
            abort(404)
        cursor = db.execute("INSERT INTO meals(user_id,day,meal_type,food_id,quantity) VALUES(?,?,?,?,?)", (
            current_user_id(), day, request.form.get("meal_type", "Refeição"),
            food["id"], quantity
        ))
        db.commit()
    remember_undo(
        "meal_add", {"id": cursor.lastrowid, "day": day},
        "Refeição registrada e metas atualizadas."
    )
    return redirect(url_for("dashboard", day=day))


@app.post("/meal/repeat-yesterday")
@login_required
def repeat_yesterday_meals():
    day = tracking_day(request.form.get("day"))
    source_day = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    with db_conn() as db:
        existing = db.execute(
            "SELECT COUNT(*) total FROM meals WHERE user_id=? AND day=?",
            (current_user_id(), day),
        ).fetchone()["total"]
        if existing:
            flash("Para evitar refeições duplicadas, repita o dia anterior somente quando o dia atual estiver vazio.")
            return redirect(url_for("dashboard", day=day))
        source = db.execute("""
            SELECT meal_type,food_id,quantity FROM meals
            WHERE user_id=? AND day=? ORDER BY id
        """, (current_user_id(), source_day)).fetchall()
        if not source:
            flash("Não há refeições registradas no dia anterior para repetir.")
            return redirect(url_for("dashboard", day=day))
        inserted_ids = []
        for row in source[:100]:
            cursor = db.execute(
                "INSERT INTO meals(user_id,day,meal_type,food_id,quantity) VALUES(?,?,?,?,?)",
                (current_user_id(), day, row["meal_type"], row["food_id"], row["quantity"]),
            )
            inserted_ids.append(cursor.lastrowid)
        db.commit()
    remember_undo(
        "repeat_meals", {"ids": inserted_ids, "day": day},
        f"{len(inserted_ids)} refeição(ões) do dia anterior foram copiadas."
    )
    return redirect(url_for("dashboard", day=day))


@app.post("/habit/quick")
@login_required
def quick_habit():
    day = tracking_day(request.form.get("day"))
    action = request.form.get("action")
    with db_conn() as db:
        current = db.execute(
            "SELECT * FROM habits WHERE user_id=? AND day=?", (current_user_id(), day)
        ).fetchone()
        previous = dict(current) if current else None
        water = current["water"] if current else 0
        sleep = current["sleep"] if current else 0
        trained = current["trained"] if current else 0
        appetite = current["appetite"] if current else 0
        if action == "water":
            water = min(10, water + .25)
            message = f"Água atualizada para {water:.2f} L."
        elif action == "water_remove":
            water = max(0, water - .25)
            message = f"Água corrigida para {water:.2f} L."
        elif action == "trained":
            trained = 1
            message = "Treino marcado como concluído."
        elif action == "untrained":
            trained = 0
            message = "Treino desmarcado."
        else:
            abort(400)
        db.execute("""
            INSERT INTO habits(user_id,day,water,sleep,trained,appetite) VALUES(?,?,?,?,?,?)
            ON CONFLICT(user_id,day) DO UPDATE SET water=excluded.water,sleep=excluded.sleep,
            trained=excluded.trained,appetite=excluded.appetite
        """, (current_user_id(), day, water, sleep, trained, appetite))
        db.commit()
    remember_undo("habit_restore", {"day": day, "previous": previous}, message)
    return redirect(url_for("dashboard", day=day))


@app.post("/progress/quick-weight")
@login_required
def quick_weight():
    day = tracking_day(request.form.get("day"))
    try:
        weight = float(request.form.get("weight", 0))
    except ValueError:
        weight = 0
    if not 25 <= weight <= 350:
        flash("Informe um peso válido entre 25 e 350 kg.")
        return redirect(url_for("dashboard", day=day))
    with db_conn() as db:
        current = db.execute(
            "SELECT weight FROM weights WHERE user_id=? AND day=?", (current_user_id(), day)
        ).fetchone()
        db.execute(
            "INSERT OR REPLACE INTO weights(user_id,day,weight) VALUES(?,?,?)",
            (current_user_id(), day, weight),
        )
        db.commit()
    remember_undo(
        "weight_restore", {"day": day, "previous": current["weight"] if current else None},
        "Peso registrado. As previsões foram recalculadas."
    )
    return redirect(url_for("dashboard", day=day))


@app.post("/meal/delete/<int:item_id>")
@login_required
def delete_meal(item_id):
    day = tracking_day(request.form.get("day"))
    with db_conn() as db:
        meal = db.execute(
            "SELECT * FROM meals WHERE id=? AND user_id=?", (item_id, current_user_id())
        ).fetchone()
        if not meal:
            return redirect(url_for("dashboard", day=day))
        db.execute("DELETE FROM meals WHERE id=? AND user_id=?", (item_id, current_user_id()))
        db.commit()
    remember_undo(
        "meal_delete", {"day": day, "meal": dict(meal)},
        "Refeição removida."
    )
    return redirect(url_for("dashboard", day=day))


@app.post("/habit")
@login_required
def save_habit():
    day = tracking_day(request.form.get("day"))
    with db_conn() as db:
        current = db.execute(
            "SELECT * FROM habits WHERE user_id=? AND day=?", (current_user_id(), day)
        ).fetchone()
        db.execute("""INSERT INTO habits(user_id,day,water,sleep,trained,appetite) VALUES(?,?,?,?,?,?)
                      ON CONFLICT(user_id,day) DO UPDATE SET water=excluded.water,sleep=excluded.sleep,
                      trained=excluded.trained,appetite=excluded.appetite""", (
            current_user_id(), day, float(request.form.get("water") or 0),
            float(request.form.get("sleep") or 0), 1 if request.form.get("trained") else 0,
            int(request.form.get("appetite") or 0)
        ))
        db.commit()
    remember_undo(
        "habit_restore", {"day": day, "previous": dict(current) if current else None},
        "Hábitos do dia atualizados."
    )
    return redirect(url_for("dashboard", day=day))


@app.route("/progress", methods=["GET", "POST"])
@login_required
def progress():
    user_id = current_user_id()
    with db_conn() as db:
        if request.method == "POST":
            action = request.form["action"]
            if action == "weight":
                db.execute("INSERT OR REPLACE INTO weights(user_id,day,weight) VALUES(?,?,?)",
                           (user_id, request.form["day"], float(request.form["weight"])))
            elif action == "measure":
                fields = [request.form.get(x) or None for x in ("arm","chest","waist","abdomen","hip","thigh","calf","shoulders")]
                db.execute("""INSERT INTO measurements(user_id,day,arm,chest,waist,abdomen,hip,thigh,calf,shoulders,notes)
                              VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (user_id, request.form["day"], *fields, request.form.get("notes", "")))
            elif action == "photo":
                try:
                    filename = save_user_image(request.files.get("photo"), "evolucao")
                except ValueError as exc:
                    flash(str(exc))
                    return redirect(url_for("progress"))
                db.execute("INSERT INTO photos(user_id,day,angle,filename,notes) VALUES(?,?,?,?,?)",
                           (user_id, request.form["day"], request.form["angle"], filename, request.form.get("notes", "")))
            db.commit()
            flash("Evolução registrada.")
            return redirect(url_for("progress"))
        weights = db.execute("SELECT * FROM weights WHERE user_id=? ORDER BY day", (user_id,)).fetchall()
        measures = db.execute("SELECT * FROM measurements WHERE user_id=? ORDER BY day DESC,id DESC", (user_id,)).fetchall()
        photos = db.execute("SELECT * FROM photos WHERE user_id=? ORDER BY day DESC,id DESC", (user_id,)).fetchall()
        predictions, weekly_rate = weight_predictions(weights)
    return render_template("progress.html", today=today(), weights=weights, measures=measures,
                           photos=photos, predictions=predictions, weekly_rate=weekly_rate)


@app.get("/uploads/<int:user_id>/<path:filename>")
@login_required
def uploaded_file(user_id, filename):
    if user_id != current_user_id():
        abort(403)
    return send_from_directory(UPLOAD_DIR / str(user_id), filename)


@app.post("/photo/delete/<int:item_id>")
@login_required
def delete_photo(item_id):
    with db_conn() as db:
        photo = db.execute("SELECT * FROM photos WHERE id=? AND user_id=?", (item_id, current_user_id())).fetchone()
        if photo:
            path = UPLOAD_DIR / str(current_user_id()) / photo["filename"]
            path.unlink(missing_ok=True)
            db.execute("DELETE FROM photos WHERE id=?", (item_id,))
            db.commit()
    flash("Foto removida.")
    return redirect(url_for("progress"))


@app.route("/workouts", methods=["GET", "POST"])
@login_required
def workouts():
    user_id = current_user_id()
    with db_conn() as db:
        if request.method == "POST":
            action = request.form["action"]
            if action == "exercise":
                try:
                    image_filename = save_user_image(request.files.get("image"), "exercicio") if request.files.get("image") else ""
                except ValueError as exc:
                    flash(str(exc))
                    return redirect(url_for("workouts"))
                db.execute("""INSERT INTO exercises(user_id,name,muscle,description,image_filename,video_url)
                              VALUES(?,?,?,?,?,?)""", (user_id, request.form["name"], request.form.get("muscle", ""),
                              request.form.get("description", ""), image_filename, request.form.get("video_url", "")))
            else:
                exercise = db.execute("SELECT id FROM exercises WHERE id=? AND user_id=?", (request.form["exercise_id"], user_id)).fetchone()
                if not exercise:
                    abort(404)
                db.execute("""INSERT INTO workouts(user_id,day,exercise_id,sets,reps,load,notes)
                              VALUES(?,?,?,?,?,?,?)""", (user_id, request.form["day"], exercise["id"],
                              int(request.form["sets"]), int(request.form["reps"]), float(request.form.get("load") or 0),
                              request.form.get("notes", "")))
            db.commit()
            flash("Registro de treino atualizado.")
            return redirect(url_for("workouts"))
        exercises = db.execute("SELECT * FROM exercises WHERE user_id=? ORDER BY muscle,name", (user_id,)).fetchall()
        history = db.execute("""SELECT w.*,e.name exercise,e.muscle FROM workouts w JOIN exercises e ON e.id=w.exercise_id
                              WHERE w.user_id=? ORDER BY w.day DESC,w.id DESC LIMIT 150""", (user_id,)).fetchall()
        strength = db.execute("""SELECT e.name,MAX(w.load) max_load,COUNT(*) sessions FROM workouts w JOIN exercises e ON e.id=w.exercise_id
                               WHERE w.user_id=? GROUP BY e.id ORDER BY sessions DESC LIMIT 8""", (user_id,)).fetchall()
    return render_template("workouts.html", today=today(), exercises=exercises, workouts=history, strength=strength)


@app.post("/exercise/delete/<int:item_id>")
@login_required
def delete_exercise(item_id):
    with db_conn() as db:
        used = db.execute("SELECT COUNT(*) n FROM workouts WHERE user_id=? AND exercise_id=?", (current_user_id(), item_id)).fetchone()["n"]
        if used:
            flash("Este exercício já possui treinos registrados e não pode ser excluído.")
        else:
            ex = db.execute("SELECT image_filename FROM exercises WHERE id=? AND user_id=?", (item_id, current_user_id())).fetchone()
            if ex and ex["image_filename"]:
                (UPLOAD_DIR / str(current_user_id()) / ex["image_filename"]).unlink(missing_ok=True)
            db.execute("DELETE FROM exercises WHERE id=? AND user_id=?", (item_id, current_user_id()))
            db.commit()
    return redirect(url_for("workouts"))


def planner_data(db, user_id):
    ps = db.execute("SELECT * FROM plan_settings WHERE user_id=?", (user_id,)).fetchone()
    raw = db.execute("""SELECT p.*,f.serving food_serving,f.calories food_calories,f.protein food_protein,
                        f.carbs food_carbs,f.fat food_fat,f.unit food_unit
                        FROM plan_items p LEFT JOIN foods f ON f.id=p.food_id
                        WHERE p.user_id=? ORDER BY p.category,p.name""", (user_id,)).fetchall()
    items, total, kcal_day, protein_day, carbs_day, fat_day = [], 0, 0, 0, 0, 0
    for row in raw:
        item = dict(row)
        required = row["daily_qty"] * ps["days"]
        to_buy = max(0, required - row["current_stock"])
        packages = math.ceil(to_buy / row["package_qty"]) if row["package_qty"] > 0 else 0
        cost = packages * row["package_price"]
        factor = row["daily_qty"] / row["food_serving"] if row["food_serving"] and row["unit"] in ("g","ml") else 0
        item_kcal = (row["food_calories"] or 0) * factor
        item_protein = (row["food_protein"] or 0) * factor
        item_carbs = (row["food_carbs"] or 0) * factor
        item_fat = (row["food_fat"] or 0) * factor
        item.update(required=required,to_buy=to_buy,packages=packages,cost=cost,
                    remaining=max(0,row["current_stock"]+packages*row["package_qty"]-row["daily_qty"]*ps["completed_days"]),
                    daily_kcal=item_kcal,daily_protein=item_protein,daily_carbs=item_carbs,daily_fat=item_fat)
        items.append(item)
        total += cost; kcal_day += item_kcal; protein_day += item_protein; carbs_day += item_carbs; fat_day += item_fat
    marmitas = ps["days"] * ps["meals_per_day"]
    return ps, items, total, marmitas, kcal_day, protein_day, carbs_day, fat_day


@app.route("/planner", methods=["GET", "POST"])
@login_required
def planner():
    user_id = current_user_id()
    with db_conn() as db:
        if request.method == "POST":
            action = request.form["action"]
            if action == "settings":
                db.execute("UPDATE plan_settings SET days=?,meals_per_day=?,completed_days=? WHERE user_id=?", (
                    max(1,int(request.form["days"])), max(1,int(request.form["meals_per_day"])),
                    max(0,int(request.form.get("completed_days") or 0)), user_id))
            elif action == "add":
                food_id = request.form.get("food_id") or None
                db.execute("""INSERT INTO plan_items(user_id,food_id,name,unit,daily_qty,package_qty,package_price,category,current_stock,notes)
                              VALUES(?,?,?,?,?,?,?,?,?,?)""", (user_id,food_id,request.form["name"],request.form["unit"],
                              float(request.form["daily_qty"]),float(request.form["package_qty"]),float(request.form["package_price"]),
                              request.form["category"],float(request.form.get("current_stock") or 0),request.form.get("notes", "")))
            else:
                food_id = request.form.get("food_id") or None
                db.execute("""UPDATE plan_items SET food_id=?,name=?,unit=?,daily_qty=?,package_qty=?,package_price=?,category=?,current_stock=?,notes=?
                              WHERE id=? AND user_id=?""", (food_id,request.form["name"],request.form["unit"],float(request.form["daily_qty"]),
                              float(request.form["package_qty"]),float(request.form["package_price"]),request.form["category"],
                              float(request.form.get("current_stock") or 0),request.form.get("notes", ""),request.form["item_id"],user_id))
            db.commit(); flash("Planejamento mensal atualizado.")
            return redirect(url_for("planner"))
        ps, items, total, marmitas, kcal_day, protein_day, carbs_day, fat_day = planner_data(db, user_id)
        foods_all = db.execute("SELECT * FROM foods WHERE user_id=? ORDER BY name", (user_id,)).fetchall()
    return render_template("planner.html", ps=ps,items=items,total=total,marmitas=marmitas,
                           cost_day=total/ps["days"],cost_meal=total/marmitas if marmitas else 0,
                           kcal_day=kcal_day,protein_day=protein_day,carbs_day=carbs_day,fat_day=fat_day,foods_all=foods_all)


@app.post("/planner/delete/<int:item_id>")
@login_required
def planner_delete(item_id):
    with db_conn() as db:
        db.execute("DELETE FROM plan_items WHERE id=? AND user_id=?", (item_id,current_user_id()))
        db.commit()
    flash("Produto removido do planejamento.")
    return redirect(url_for("planner"))


@app.route("/markets", methods=["GET", "POST"])
@login_required
def markets():
    user_id = current_user_id()
    with db_conn() as db:
        if request.method == "POST":
            action = request.form["action"]
            if action == "store":
                db.execute("INSERT INTO stores(user_id,name) VALUES(?,?)", (user_id,request.form["name"].strip()))
            else:
                db.execute("""INSERT INTO store_prices(user_id,store_id,plan_item_id,package_price,updated_at)
                              VALUES(?,?,?,?,?) ON CONFLICT(user_id,store_id,plan_item_id)
                              DO UPDATE SET package_price=excluded.package_price,updated_at=excluded.updated_at""", (
                    user_id,request.form["store_id"],request.form["plan_item_id"],float(request.form["package_price"]),datetime.now().isoformat(timespec="seconds")))
            db.commit(); flash("Comparador atualizado.")
            return redirect(url_for("markets"))
        stores = db.execute("SELECT * FROM stores WHERE user_id=? ORDER BY name", (user_id,)).fetchall()
        ps, items, *_ = planner_data(db, user_id)
        prices = db.execute("""SELECT sp.*,s.name store,p.name item FROM store_prices sp
                             JOIN stores s ON s.id=sp.store_id JOIN plan_items p ON p.id=sp.plan_item_id
                             WHERE sp.user_id=? ORDER BY s.name,p.name""", (user_id,)).fetchall()
        ranking = []
        for store in stores:
            total = 0; missing = []
            for item in items:
                price = db.execute("SELECT package_price FROM store_prices WHERE user_id=? AND store_id=? AND plan_item_id=?",
                                   (user_id,store["id"],item["id"])).fetchone()
                if price:
                    total += item["packages"] * price["package_price"]
                else:
                    missing.append(item["name"])
            ranking.append({"name":store["name"],"total":total,"missing":missing,"complete":not missing})
        ranking.sort(key=lambda x: (not x["complete"], x["total"]))
    return render_template("markets.html", stores=stores,items=items,prices=prices,ranking=ranking)


@app.post("/store/delete/<int:item_id>")
@login_required
def delete_store(item_id):
    with db_conn() as db:
        db.execute("DELETE FROM stores WHERE id=? AND user_id=?", (item_id,current_user_id()))
        db.commit()
    return redirect(url_for("markets"))


@app.route("/calendar", methods=["GET", "POST"])
@login_required
def calendar_page():
    user_id = current_user_id()
    with db_conn() as db:
        if request.method == "POST":
            action = request.form["action"]
            if action == "meal":
                db.execute("""INSERT INTO calendar_meals(user_id,day,time,title,food_id,quantity,notes)
                              VALUES(?,?,?,?,?,?,?)""", (user_id,request.form["day"],request.form["time"],request.form["title"],
                              request.form.get("food_id") or None,float(request.form.get("quantity") or 0),request.form.get("notes", "")))
            else:
                db.execute("INSERT INTO reminders(user_id,title,time,days,enabled) VALUES(?,?,?,?,1)",
                           (user_id,request.form["title"],request.form["time"],request.form.get("days", "Todos os dias")))
            db.commit(); flash("Agenda atualizada.")
            return redirect(url_for("calendar_page"))
        events = db.execute("""SELECT c.*,f.name food,f.serving,f.calories FROM calendar_meals c LEFT JOIN foods f ON f.id=c.food_id
                             WHERE c.user_id=? ORDER BY c.day,c.time""", (user_id,)).fetchall()
        reminders = db.execute("SELECT * FROM reminders WHERE user_id=? ORDER BY time", (user_id,)).fetchall()
        foods_all = db.execute("SELECT * FROM foods WHERE user_id=? ORDER BY name", (user_id,)).fetchall()
    reminder_json = [dict(x) for x in reminders if x["enabled"]]
    return render_template("calendar.html", today=today(),events=events,reminders=reminders,reminder_json=reminder_json,foods_all=foods_all)


@app.post("/calendar/delete/<int:item_id>")
@login_required
def delete_calendar_item(item_id):
    with db_conn() as db:
        db.execute("DELETE FROM calendar_meals WHERE id=? AND user_id=?", (item_id,current_user_id()))
        db.commit()
    return redirect(url_for("calendar_page"))


@app.post("/reminder/delete/<int:item_id>")
@login_required
def delete_reminder(item_id):
    with db_conn() as db:
        db.execute("DELETE FROM reminders WHERE id=? AND user_id=?", (item_id,current_user_id()))
        db.commit()
    return redirect(url_for("calendar_page"))


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings_page():
    with db_conn() as db:
        if request.method == "POST":
            db.execute("""UPDATE settings SET age=?,height=?,start_weight=?,goal_weight=?,final_goal=?,calories=?,protein=?,carbs=?,fat=?,water=?,weekly_target=?
                          WHERE user_id=?""", (int(request.form["age"]),float(request.form["height"]),float(request.form["start_weight"]),
                          float(request.form["goal_weight"]),float(request.form["final_goal"]),int(request.form["calories"]),
                          int(request.form["protein"]),int(request.form["carbs"]),int(request.form["fat"]),float(request.form["water"]),
                          float(request.form["weekly_target"]),current_user_id()))
            db.execute("UPDATE users SET name=? WHERE id=?", (request.form["name"].strip(),current_user_id()))
            db.commit(); flash("Metas e perfil atualizados.")
            return redirect(url_for("settings_page"))
        s = get_settings(db,current_user_id())
    return render_template("settings.html", s=s)


@app.get("/report.pdf")
@login_required
def daily_report():
    day = request.args.get("day", today())
    with db_conn() as db:
        s = get_settings(db,current_user_id())
        totals = daily_totals(db,current_user_id(),day)
        meals = db.execute("""SELECT m.*,f.name,f.serving,f.unit,f.calories,f.protein,f.carbs,f.fat
                              FROM meals m JOIN foods f ON f.id=m.food_id WHERE m.user_id=? AND m.day=? ORDER BY m.meal_type,m.id""",
                           (current_user_id(),day)).fetchall()
        habit = db.execute("SELECT * FROM habits WHERE user_id=? AND day=?", (current_user_id(),day)).fetchone()
    buffer=BytesIO(); pdf=canvas.Canvas(buffer,pagesize=A4); width,height=A4; y=height-1.7*cm
    pdf.setTitle(f"TITAN - Relatório {day}"); pdf.setFont("Helvetica-Bold",17); pdf.drawString(1.6*cm,y,"PROJETO TITAN - RELATÓRIO NUTRICIONAL"); y-=.8*cm
    pdf.setFont("Helvetica",10); pdf.drawString(1.6*cm,y,f"Usuário: {g.user['name']} | Data: {day}"); y-=.8*cm
    pdf.setFont("Helvetica-Bold",12); pdf.drawString(1.6*cm,y,"Resumo do dia"); y-=.55*cm
    for label,key,goal,unit in [("Calorias","calories",s["calories"],"kcal"),("Proteínas","protein",s["protein"],"g"),("Carboidratos","carbs",s["carbs"],"g"),("Gorduras","fat",s["fat"],"g")]:
        pdf.setFont("Helvetica",10); pdf.drawString(1.8*cm,y,f"{label}: {totals[key]:.1f} / {goal} {unit}"); y-=.42*cm
    y-=.25*cm; pdf.setFont("Helvetica-Bold",12); pdf.drawString(1.6*cm,y,"Alimentos e valores calculados"); y-=.55*cm
    for m in meals:
        factor=m["quantity"]/m["serving"]
        line=f"{m['meal_type']} - {m['name']}: {m['quantity']:.0f} {m['unit']} | {m['calories']*factor:.0f} kcal | P {m['protein']*factor:.1f} C {m['carbs']*factor:.1f} G {m['fat']*factor:.1f}"
        pdf.setFont("Helvetica",8.8); pdf.drawString(1.8*cm,y,line[:115]); y-=.4*cm
        if y<2.2*cm: pdf.showPage(); y=height-1.7*cm
    y-=.2*cm; pdf.setFont("Helvetica-Bold",11); pdf.drawString(1.6*cm,y,"Hábitos"); y-=.45*cm; pdf.setFont("Helvetica",9)
    pdf.drawString(1.8*cm,y,f"Água: {(habit['water'] if habit else 0):.1f} L | Sono: {(habit['sleep'] if habit else 0):.1f} h | Treino: {'Sim' if habit and habit['trained'] else 'Não'}")
    pdf.save(); buffer.seek(0)
    return send_file(buffer,as_attachment=True,download_name=f"titan_{day}.pdf",mimetype="application/pdf")


@app.get("/planner.pdf")
@login_required
def planner_pdf():
    with db_conn() as db:
        ps,items,total,marmitas,kcal_day,protein_day,carbs_day,fat_day=planner_data(db,current_user_id())
    buffer=BytesIO(); pdf=canvas.Canvas(buffer,pagesize=A4); width,height=A4; y=height-1.7*cm
    pdf.setTitle("TITAN - Lista de compras"); pdf.setFont("Helvetica-Bold",17); pdf.drawString(1.6*cm,y,"PROJETO TITAN - LISTA DE COMPRAS"); y-=.7*cm
    pdf.setFont("Helvetica",9.5); pdf.drawString(1.6*cm,y,f"{ps['days']} dias | {marmitas} marmitas | Estimativa nutricional diária vinculada: {kcal_day:.0f} kcal e {protein_day:.0f} g de proteína"); y-=.75*cm
    category=None
    for item in items:
        if y<2.4*cm: pdf.showPage(); y=height-1.7*cm
        if item["category"]!=category: category=item["category"]; pdf.setFont("Helvetica-Bold",11); pdf.drawString(1.6*cm,y,category); y-=.48*cm
        pdf.setFont("Helvetica",9); cost=f"R$ {item['cost']:.2f}".replace('.',','); pdf.drawString(1.8*cm,y,f"[ ] {item['name']}: {item['packages']} embalagem(ns) - {cost}"); y-=.36*cm
        pdf.setFillGray(.35); pdf.drawString(2.1*cm,y,f"Necessário: {item['required']:.0f} {item['unit']} | {item['daily_kcal']:.0f} kcal/dia vinculadas"); pdf.setFillGray(0); y-=.45*cm
    y-=.15*cm; pdf.setFont("Helvetica-Bold",12); pdf.drawString(1.6*cm,y,(f"TOTAL ESTIMADO: R$ {total:.2f}").replace('.',',')); pdf.save(); buffer.seek(0)
    return send_file(buffer,as_attachment=True,download_name="titan_lista_compras.pdf",mimetype="application/pdf")


@app.get("/calendar.ics")
@login_required
def calendar_ics():
    with db_conn() as db:
        events=db.execute("SELECT * FROM calendar_meals WHERE user_id=? ORDER BY day,time",(current_user_id(),)).fetchall()
    lines=["BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//Projeto TITAN//PT-BR"]
    for event in events:
        dt=event["day"].replace('-','')+'T'+event["time"].replace(':','')+'00'
        lines += ["BEGIN:VEVENT",f"UID:titan-{event['id']}@local",f"DTSTART:{dt}",f"SUMMARY:{event['title']}",f"DESCRIPTION:{event['notes'] or ''}","END:VEVENT"]
    lines.append("END:VCALENDAR")
    buffer=BytesIO("\r\n".join(lines).encode('utf-8')); buffer.seek(0)
    return send_file(buffer,as_attachment=True,download_name="titan_refeicoes.ics",mimetype="text/calendar")


@app.get("/export.zip")
@login_required
def export_user_data():
    user_id=current_user_id()
    tables=["settings","foods","meals","weights","measurements","habits","photos","exercises","workouts","plan_settings","plan_items","stores","store_prices","calendar_meals","reminders"]
    with NamedTemporaryFile(suffix=".zip",delete=False) as tmp:
        tmp_path=Path(tmp.name)
    with zipfile.ZipFile(tmp_path,"w",zipfile.ZIP_DEFLATED) as z:
        with db_conn() as db:
            payload={"exported_at":datetime.now().isoformat(),"user":{"name":g.user["name"],"email":g.user["email"]},"data":{}}
            for table in tables:
                payload["data"][table]=[dict(r) for r in db.execute(f"SELECT * FROM {table} WHERE user_id=?",(user_id,)).fetchall()]
            z.writestr("dados_titan.json",json.dumps(payload,ensure_ascii=False,indent=2))
        folder=UPLOAD_DIR/str(user_id)
        if folder.exists():
            for path in folder.iterdir():
                if path.is_file(): z.write(path,Path("imagens")/path.name)
    return send_file(tmp_path,as_attachment=True,download_name="backup_usuario_titan.zip",mimetype="application/zip")


@app.errorhandler(413)
def too_large(_):
    flash("A imagem excede o limite de 8 MB.")
    return redirect(request.referrer or url_for("dashboard"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
