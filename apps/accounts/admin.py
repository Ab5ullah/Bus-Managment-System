"""
Django Admin Configuration for Accounts & Authentication.
"""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import UserCreationForm, UserChangeForm
from .models import User, PassengerProfile, OTPVerification, SavedPassenger


class CustomUserCreationForm(UserCreationForm):
    """Admin form for creating a new user with phone, email, and role."""
    class Meta:
        model = User
        fields = ('phone', 'email', 'role', 'is_staff', 'is_active', 'is_verified')


class CustomUserChangeForm(UserChangeForm):
    """Admin form for updating existing user details."""
    class Meta:
        model = User
        fields = '__all__'


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = CustomUserChangeForm
    add_form = CustomUserCreationForm

    list_display = (
        'phone', 'email', 'role', 'is_verified', 'is_active',
        'is_staff', 'failed_login_attempts', 'is_locked_status', 'created_at'
    )
    list_filter = ('role', 'is_verified', 'is_active', 'is_staff')
    search_fields = ('phone', 'email')
    ordering = ('-created_at',)

    fieldsets = (
        ('Credentials & Contact', {'fields': ('phone', 'email', 'password')}),
        ('Role & Permissions', {'fields': ('role', 'is_verified', 'is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Security & Lockout', {'fields': ('failed_login_attempts', 'locked_until')}),
        ('Timestamps', {'fields': ('created_at', 'updated_at')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('phone', 'email', 'role', 'is_staff', 'is_active', 'is_verified', 'password1', 'password2'),
        }),
    )
    readonly_fields = ('created_at', 'updated_at')

    def is_locked_status(self, obj):
        return obj.is_locked
    is_locked_status.boolean = True
    is_locked_status.short_description = "Locked?"

    actions = ['unlock_accounts', 'verify_accounts']

    @admin.action(description="Unlock selected accounts and reset failed attempts")
    def unlock_accounts(self, request, queryset):
        queryset.update(failed_login_attempts=0, locked_until=None)

    @admin.action(description="Mark selected users as verified")
    def verify_accounts(self, request, queryset):
        queryset.update(is_verified=True)


@admin.register(PassengerProfile)
class PassengerProfileAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'get_phone', 'gender', 'cnic_id_number', 'emergency_contact', 'created_at')
    search_fields = ('full_name', 'user__phone', 'user__email', 'cnic_id_number')
    list_filter = ('gender',)

    def get_phone(self, obj):
        return obj.user.phone
    get_phone.short_description = "Phone"


@admin.register(OTPVerification)
class OTPVerificationAdmin(admin.ModelAdmin):
    list_display = ('identifier', 'purpose', 'is_used', 'is_expired_status', 'expires_at', 'created_at')
    list_filter = ('purpose', 'is_used')
    search_fields = ('identifier',)
    readonly_fields = ('otp_hash', 'created_at')

    def is_expired_status(self, obj):
        from django.utils import timezone
        return timezone.now() > obj.expires_at
    is_expired_status.boolean = True
    is_expired_status.short_description = "Expired?"


@admin.register(SavedPassenger)
class SavedPassengerAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'get_owner', 'gender', 'age', 'cnic_id_number', 'created_at')
    search_fields = ('full_name', 'user__phone', 'cnic_id_number')
    list_filter = ('gender',)

    def get_owner(self, obj):
        return obj.user.phone
    get_owner.short_description = "Owner Phone"
