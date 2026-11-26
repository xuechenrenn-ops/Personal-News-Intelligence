"""Shared, bounded orthopaedic vocabulary for acquisition and screening."""
import re

ORTHOPAEDIC = re.compile(
    r"orthop[ae]edi|musculoskelet|arthroplast|arthroscop|fracture|menisc|ligament|"
    r"osteotom|laminoplast|osteopor|lumbar|cervical|spine|gait|rotator cuff|"
    r"adhesive capsulitis|frozen shoulder|plantar fasciitis|anterior cruciate|"
    r"cartilage|knee|patellofemoral|\bACL(?:R)?\b|\bHTO\b|骨科|截骨|前交叉韧带|"
    r"半月板|肩袖|关节置换|步态|骨折|髌股",
    re.I,
)
CLINICAL_AI = re.compile(
    r"artificial intelligence|machine learning|deep learning|large language model|"
    r"neural network|clinical decision support|wearable|ehealth|digital health|"
    r"electronic health record|人工智能|机器学习|深度学习|大语言模型|数字康复",
    re.I,
)
CLINICAL_CONTEXT = re.compile(
    r"patient|clinical|medical|hospital|health ?care|electronic health|rehab|"
    r"clinic|surger|diagnos|患者|临床|医疗|手术",
    re.I,
)
OUTSIDE_SCOPE = re.compile(
    r"orthodont|dent[ai]l|dentistr|periodont|maxillofacial|battery|cathode|marketing",
    re.I,
)
ORTHOPAEDIC_MESH = {
    "anterior cruciate ligament reconstruction", "anterior cruciate ligament injuries",
    "osteotomy", "orthopedics", "sports medicine", "orthopedic procedures",
    "arthroplasty, replacement, knee", "menisci, tibial",
}


def research_lane(title, mesh=()):
    if OUTSIDE_SCOPE.search(title):
        return None
    is_ortho = bool(ORTHOPAEDIC.search(title) or ORTHOPAEDIC_MESH.intersection(str(m).casefold() for m in mesh))
    is_ai = bool(CLINICAL_AI.search(title))
    if is_ortho:
        return "ortho-ai" if is_ai else "clinical"
    if is_ai and CLINICAL_CONTEXT.search(title):
        return "medical-ai"
    return None
