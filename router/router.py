from registry.models import MODELS, ModelInfo


def find_models_by_skill(skill: str) -> list[ModelInfo]:
    """
    Devuelve todos los modelos registrados que poseen una skill.
    No elimina ni limita modelos.
    """
    return [
        model
        for model in MODELS.values()
        if skill in model.skills
    ]


def find_model(name: str) -> ModelInfo | None:
    """Busca un modelo por nombre."""
    return MODELS.get(name)
