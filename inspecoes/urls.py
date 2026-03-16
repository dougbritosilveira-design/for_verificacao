from django.urls import path

from . import views

app_name = 'inspecoes'

urlpatterns = [
    path('formularios/novo/', views.selection_view, name='selection'),
    path('formularios/<int:pk>/editar/', views.form_edit_view, name='form-edit'),
    path('formularios/<int:pk>/validar/', views.form_validate_view, name='form-validate'),
    path('formularios/<int:pk>/pdf/', views.form_download_pdf_view, name='form-download-pdf'),
    path('formularios/<int:pk>/certificado/', views.form_download_certificate_view, name='form-download-certificate'),
    path('formularios/<int:pk>/enviar-sap/', views.form_send_sap_view, name='form-send-sap'),
    path('formularios/<int:pk>/', views.detail_view, name='detail'),
    path('historico/', views.history_view, name='history'),
    path('equipamentos/prazos/', views.equipment_deadlines_view, name='equipment-deadlines'),
    path('notificacoes/', views.notifications_view, name='notifications'),
]
