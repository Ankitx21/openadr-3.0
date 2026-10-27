from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from .models import DashboardAccount, Team


class DashboardAccountForm(UserCreationForm):
    role = forms.ChoiceField(choices=[('team', 'Team'), ('jury', 'Jury'), ('organizer', 'Organizer')], initial='team')
    team = forms.ModelChoiceField(queryset=Team.objects.filter(dashboard_account__isnull=True), required=False)

    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = ('username', 'role', 'team', 'password1', 'password2')

    def clean(self):
        data = super().clean()
        team = data.get('team')
        if (data.get('role') == 'team') != bool(team):
            self.add_error('team', 'Select a team for team accounts only.')
        if team and DashboardAccount.objects.filter(team=team).exists():
            self.add_error('team', 'This team already has a dashboard account. Reset its password instead.')
        if team and data.get('password1') == team.token:
            self.add_error('password1', 'Use a password different from the team API token.')
        return data
