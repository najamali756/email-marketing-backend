from Stores.mixins import StoreContextMixin
from Stores.permissions import HasStoreContext


class StoreAuthenticatedMixin(StoreContextMixin):
    permission_classes = [HasStoreContext]
