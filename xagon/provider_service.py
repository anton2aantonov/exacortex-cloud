"""Mock-only structured provider orchestration with explicit offline fallback."""
from dataclasses import dataclass
from .provider_contract import prepare_from_store,validate_response,require_transport_permission
from .intelligence_chat import OfflineProvider,ContextBuilder,CitationValidator


@dataclass(frozen=True)
class ModelConfiguration:
    provider: str = 'mock'
    model: str = 'fixture-only'
    cloud_enabled: bool = False


class FixtureProvider:
    def __init__(self,response=None,error=None):self.response=response;self.error=error
    def generate(self,request):
        if self.error:raise self.error
        return self.response


def run_fixture(store,question,provider,material_ids=None,configuration=None):
    config=configuration or ModelConfiguration()
    if config.cloud_enabled or config.provider!='mock':raise PermissionError('RC3 real providers disabled')
    # No arbitrary adapters are auto-loaded and no network transport exists.
    if type(provider) is not FixtureProvider:raise ValueError('Only explicit local fixture provider accepted')
    require_transport_permission('mock')
    request,cost=prepare_from_store(store,question,material_ids)
    try:
        result=validate_response(provider.generate(request),request)
        return dict(result,mode='mock_structured',model=config.model,estimate=cost,real_ai=False,fallback=False)
    except (ValueError,TimeoutError,RuntimeError):
        selected=store.selected_materials(material_ids)
        sources=CitationValidator(store).validate(ContextBuilder(store).build(question,selected),selected)
        result=OfflineProvider().answer(question,sources)
        return dict(result,fallback=True,fallback_reason='Mock provider failed validation or execution; local quotes only',real_ai=False)
