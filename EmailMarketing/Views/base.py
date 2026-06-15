from Accounts.mixins import StoreContextMixin
from Accounts.permissions import HasStoreContext


class StoreAuthenticatedMixin(StoreContextMixin):
    permission_classes = [HasStoreContext]
