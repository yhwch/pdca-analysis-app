from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('upload/', views.upload, name='upload'),
    path('compare/', views.compare, name='compare'),
    path('experiments/', views.experiments, name='experiments'),
    path('experiments/<int:experiment_id>/', views.experiment_detail, name='experiment_detail'),
    path('experiments/<int:experiment_id>/delete/', views.experiment_delete, name='experiment_delete'),
    path('experiments/<int:experiment_id>/notes/', views.experiment_update_notes, name='experiment_update_notes'),
    path('experiments/<int:experiment_id>/chart/', views.experiment_chart, name='experiment_chart'),
    path('experiments/<int:experiment_id>/rename/', views.experiment_rename, name='experiment_rename'),
    path('experiments/<int:experiment_id>/print/', views.experiment_print, name='experiment_print'),
    path('api/date-range/', views.api_date_range, name='api_date_range'),
    path('compare/chart/', views.compare_chart, name='compare_chart'),
    path('api/orders-for-product/', views.api_orders_for_product, name='api_orders_for_product'),
    path('compare/recalculate/', views.compare_recalculate, name='compare_recalculate'),
    path('compare/tools/', views.compare_tools, name='compare_tools'),
    path('compare/tools/chart/', views.compare_tools_chart, name='compare_tools_chart'),
]