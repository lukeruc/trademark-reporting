from django.contrib.auth.views import LogoutView
from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('login/', views.SignIn.as_view(), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('workspace/', views.workspace, name='workspace'),
    path('enterprise/new/', views.enterprise_edit, name='enterprise_new'),
    path('enterprise/<int:pk>/', views.enterprise_detail, name='enterprise'),
    path('enterprise/<int:pk>/edit/', views.enterprise_edit, name='enterprise_edit'),
    path('enterprise/<int:pk>/delete/', views.enterprise_delete, name='enterprise_delete'),
    path('enterprise/<int:pk>/empty/<str:kind>/', views.confirm_empty, name='confirm_empty'),
    path('enterprise/<int:pk>/entry/<str:kind>/new/', views.entry_edit, name='entry_new'),
    path('entry/<int:pk>/edit/', views.entry_edit, name='entry_edit'),
    path('entry/<int:pk>/delete/', views.entry_delete, name='entry_delete'),
    path('submit/', views.submit_report, name='submit'),
    path('request-return/', views.request_return_view, name='request_return'),
    path('submission/<int:pk>/', views.submission_detail, name='submission'),
    path('images/<uuid:pk>/', views.mark_image, name='mark_image'),
    path('manage/', views.dashboard, name='dashboard'),
    path('manage/accounts/new/', views.account_create, name='account_create'),
    path('manage/unit/<int:pk>/account/', views.account_manage, name='account_manage'),
    path('manage/unit/<int:pk>/account/delete/', views.account_delete, name='account_delete'),
    path('manage/unit/<int:pk>/return/', views.return_report, name='return_report'),
    path('manage/aggregate/', views.aggregate, name='aggregate'),
    path('manage/export/', views.export_report, name='export'),
    path('manage/entry/', views.entry_config, name='entry_config'),
    path('manage/entry/qr.png', views.entry_qr, name='entry_qr'),
]
