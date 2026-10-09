"""Gera icone.ico, usado nos dois executaveis.

Desenha um documento com um visto, nas cores da interface. Roda sozinho:

    python icone.py
"""

import os

from PIL import Image, ImageDraw

FUNDO = (7, 17, 31, 255)
PAPEL = (238, 244, 251, 255)
DOBRA = (148, 163, 184, 255)
LINHA = (148, 163, 184, 255)
VISTO = (34, 197, 94, 255)
TAMANHOS = [16, 24, 32, 48, 64, 128, 256]
LADO = 256


def desenhar():
    img = Image.new("RGBA", (LADO, LADO), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    d.rounded_rectangle([8, 8, LADO - 8, LADO - 8], radius=46, fill=FUNDO)

    # folha com o canto dobrado
    esq, topo, dir_, base, dobra = 62, 44, 196, 212, 42
    d.polygon(
        [(esq, topo), (dir_ - dobra, topo), (dir_, topo + dobra), (dir_, base), (esq, base)],
        fill=PAPEL,
    )
    d.polygon([(dir_ - dobra, topo), (dir_, topo + dobra), (dir_ - dobra, topo + dobra)], fill=DOBRA)

    # linhas de texto
    for i, y in enumerate((96, 120, 144)):
        fim = dir_ - 22 if i < 2 else dir_ - 60
        d.rounded_rectangle([esq + 20, y, fim, y + 9], radius=4, fill=LINHA)

    # visto sobre a folha
    d.line([(104, 176), (134, 204), (196, 134)], fill=VISTO, width=22, joint="curve")
    return img


def main():
    destino = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icone.ico")
    img = desenhar()
    img.save(destino, format="ICO", sizes=[(t, t) for t in TAMANHOS])
    print(f"Gerado: {destino} ({', '.join(f'{t}x{t}' for t in TAMANHOS)})")
    return destino


if __name__ == "__main__":
    main()
